"""
Phase 2 — Train the intelligence modules.

    python -m backend.ml.train

1. Personal baselines: per-operator mean/std of jerk, relative cycle time and
   idle ratio -> operator_baseline table.
2. Risk-trajectory classifier (XGBoost): P(unsafe event within 15 min).
   Evaluated on held-out shifts (S10-S12) before refitting on everything.
3. Task-time regressor (gradient boosting, quantile loss at 10/50/90%).
"""
import json

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import average_precision_score, mean_absolute_error, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder

from ..config import LABEL_MAX_AHEAD_S, LABEL_MIN_AHEAD_S, MODELS_DIR, TICK_SECONDS
from ..db import connect
from .features import RISK_FEATURES, add_shift_elapsed, compute_features, label_future_unsafe, set_effort_norm

HOLDOUT_SHIFTS = {"S10", "S11", "S12"}


def load(conn):
    tel = pd.read_sql("SELECT * FROM machine_telemetry ORDER BY machine_id, shift_id, timestamp", conn)
    shifts = pd.read_sql("SELECT * FROM shift", conn)
    ops = pd.read_sql("SELECT * FROM operator_profile", conn)
    mach = pd.read_sql("SELECT * FROM machine", conn)
    tel = add_shift_elapsed(tel, dict(zip(shifts.shift_id, shifts.start_ts)))
    return tel, ops, mach


def featurize_all(tel, baselines, fleet_med, tenure, mtype):
    parts = []
    for _, g in tel.groupby(["shift_id", "machine_id"], sort=False):
        f = compute_features(g, baselines, fleet_med, tenure, mtype)
        f["label"] = label_future_unsafe(f, LABEL_MIN_AHEAD_S // TICK_SECONDS, LABEL_MAX_AHEAD_S // TICK_SECONDS)
        parts.append(f)
    return pd.concat(parts)


def build_all_features(conn, verbose=True):
    """Two passes: first to learn fleet norms + personal baselines, then the real features."""
    tel, ops, mach = load(conn)
    tenure = dict(zip(ops.operator_id, ops.tenure_months))
    mtype = dict(zip(mach.machine_id, mach.machine_type))

    set_effort_norm({})
    f0 = featurize_all(tel, {}, {}, tenure, mtype)
    f0["mtype"] = f0["machine_id"].map(mtype)
    fleet_med = (f0.groupby("mtype")["cycle_rel"].median() * 60).to_dict()          # seconds
    effort_norm = f0.groupby("mtype")["effort_to_output"].median().to_dict()
    set_effort_norm(effort_norm)
    f0["cycle_rel"] = f0["cycle_rel"] * 60 / f0["mtype"].map(fleet_med)

    # Personal baselines (only from 'normal' operating time, i.e. not inside a label window).
    calm = f0[f0["label"] == 0]
    baselines, brows = {}, []
    for op, g in calm.groupby("operator_id"):
        for metric, col in (("jerk", "jerk_mean_5"), ("cycle_rel", "cycle_rel"), ("idle_ratio", "idle_ratio_10")):
            m, s = float(g[col].mean()), float(g[col].std())
            baselines.setdefault(op, {})[metric] = (m, s)
            brows.append(dict(operator_id=op, metric=metric, mean=m, std=s))
    conn.execute("DELETE FROM operator_baseline")
    pd.DataFrame(brows).to_sql("operator_baseline", conn, if_exists="append", index=False)
    conn.commit()
    if verbose:
        print("Personal baselines (jerk mean):",
              {op: round(b["jerk"][0], 3) for op, b in baselines.items()})

    feats = featurize_all(tel, baselines, fleet_med, tenure, mtype)
    meta = {"fleet_cycle_median": fleet_med, "effort_norm": effort_norm}
    return feats, meta


RISK_PARAMS = dict(n_estimators=350, max_depth=4, learning_rate=0.05, subsample=0.85,
                   colsample_bytree=0.85, min_child_weight=5, eval_metric="logloss", n_jobs=4)


def fit_risk(df):
    return xgb.XGBClassifier(**RISK_PARAMS).fit(df[RISK_FEATURES].astype(float), df["label"])


def train_risk(feats, verbose=True):
    params = RISK_PARAMS
    tr = feats[~feats.shift_id.isin(HOLDOUT_SHIFTS)]
    te = feats[feats.shift_id.isin(HOLDOUT_SHIFTS)]
    m = xgb.XGBClassifier(**params)
    m.fit(tr[RISK_FEATURES].astype(float), tr["label"])
    p = m.predict_proba(te[RISK_FEATURES].astype(float))[:, 1]
    report = {
        "holdout_auc": round(float(roc_auc_score(te["label"], p)), 3),
        "holdout_pr_auc": round(float(average_precision_score(te["label"], p)), 3),
        "base_rate": round(float(te["label"].mean()), 3),
    }
    if verbose:
        print(f"Risk model holdout (shifts {sorted(HOLDOUT_SHIFTS)}): {report}")
        print("  score percentiles:", {q: round(float(np.quantile(p, q)), 3) for q in (0.5, 0.9, 0.97, 0.99, 0.999)})

    # Final model on all shifts.
    final = xgb.XGBClassifier(**params)
    final.fit(feats[RISK_FEATURES].astype(float), feats["label"])
    imp = sorted(zip(RISK_FEATURES, final.feature_importances_), key=lambda t: -t[1])[:10]
    if verbose:
        print("  top global features:", [(f, round(float(v), 3)) for f, v in imp])
    report["top_global_features"] = [(f, round(float(v), 4)) for f, v in imp]
    return final, report


TASK_CAT = ["task_type", "machine_type", "archetype", "weather"]
TASK_NUM = ["estimated_time_min", "machine_age_yrs", "tenure_months", "start_h"]


def train_task(conn, verbose=True):
    t = pd.read_sql("""SELECT q.*, p.behavioral_archetype AS archetype, p.tenure_months, m.machine_type
                       FROM task_queue q JOIN operator_profile p USING(operator_id) JOIN machine m USING(machine_id)
                       WHERE q.status='completed'""", conn)
    ts = pd.to_datetime(t["assigned_start"], format="ISO8601")
    t["start_h"] = ts.dt.hour + ts.dt.minute / 60 - 6
    X, y = t[TASK_CAT + TASK_NUM], t["actual_time_min"]
    test = t["shift_id"].isin(HOLDOUT_SHIFTS)

    def fit(alpha, Xf, yf):
        pre = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore"), TASK_CAT)], remainder="passthrough")
        loss = dict(loss="quantile", alpha=alpha) if alpha != 0.5 else dict(loss="absolute_error")
        return make_pipeline(pre, GradientBoostingRegressor(n_estimators=250, max_depth=3, learning_rate=0.05, **loss)).fit(Xf, yf)

    mid = fit(0.5, X[~test], y[~test])
    lo, hi = fit(0.1, X[~test], y[~test]), fit(0.9, X[~test], y[~test])
    pred = mid.predict(X[test])
    cover = float(((y[test] >= lo.predict(X[test])) & (y[test] <= hi.predict(X[test]))).mean())
    report = {"holdout_mae_min": round(float(mean_absolute_error(y[test], pred)), 2),
              "planner_estimate_mae_min": round(float(mean_absolute_error(y[test], X[test]["estimated_time_min"])), 2),
              "interval_80_coverage": round(cover, 2)}
    if verbose:
        print(f"Task-time model holdout: {report}")
    models = {"q10": fit(0.1, X, y), "q50": fit(0.5, X, y), "q90": fit(0.9, X, y)}
    return models, report


def main():
    MODELS_DIR.mkdir(exist_ok=True)
    conn = connect()
    feats, meta = build_all_features(conn)
    risk, rrep = train_risk(feats)
    task, trep = train_task(conn)
    joblib.dump(risk, MODELS_DIR / "risk_xgb.joblib")
    joblib.dump(task, MODELS_DIR / "task_gbr.joblib")
    meta["risk_report"], meta["task_report"] = rrep, trep
    (MODELS_DIR / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Saved models -> {MODELS_DIR}")
    conn.close()


if __name__ == "__main__":
    main()
