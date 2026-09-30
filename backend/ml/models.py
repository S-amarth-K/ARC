"""
Loading + inference wrappers for the three intelligence modules:

  1. Personal baseline      -> z-scores (computed inside features.compute_features)
  2. Risk-trajectory model  -> P(unsafe event within 15 min) + per-prediction
                               contributing features (XGBoost native SHAP values)
  3. Task-time regressor    -> predicted minutes with an 80% interval, and a
                               task-delay probability
"""
import json
import math

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from ..config import MODELS_DIR
from ..db import rows
from .features import FEATURE_LABELS, RISK_FEATURES, set_effort_norm


class Intelligence:
    def __init__(self, conn):
        meta = json.loads((MODELS_DIR / "meta.json").read_text())
        self.meta = meta
        self.fleet_cycle_median = meta["fleet_cycle_median"]
        set_effort_norm(meta["effort_norm"])
        self.risk_model: xgb.XGBClassifier = joblib.load(MODELS_DIR / "risk_xgb.joblib")
        self.task_models = joblib.load(MODELS_DIR / "task_gbr.joblib")
        self.reload_baselines(conn)
        ops = rows(conn, "SELECT * FROM operator_profile")
        self.operators = {o["operator_id"]: o for o in ops}
        self.tenure = {o["operator_id"]: o["tenure_months"] for o in ops}
        self.machine_type = {m["machine_id"]: m["machine_type"] for m in rows(conn, "SELECT * FROM machine")}

    def reload_baselines(self, conn):
        self.baselines = {}
        for r in rows(conn, "SELECT * FROM operator_baseline"):
            self.baselines.setdefault(r["operator_id"], {})[r["metric"]] = (r["mean"], r["std"])

    # ------------------------------------------------------------------ risk
    def risk(self, feats: pd.DataFrame, top_k: int = 3, model=None):
        """Returns (probabilities ndarray, list of top contributing features per row)."""
        model = model or self.risk_model
        X = feats[RISK_FEATURES].astype(float)
        proba = model.predict_proba(X)[:, 1]
        # pred_contribs=True gives exact TreeSHAP values in log-odds space; last column is bias.
        contribs = model.get_booster().predict(xgb.DMatrix(X, feature_names=RISK_FEATURES), pred_contribs=True)[:, :-1]
        tops = []
        for row in contribs:
            idx = np.argsort(-row)[:top_k]
            tops.append([(RISK_FEATURES[i], round(float(row[i]), 3)) for i in idx if row[i] > 0.05])
        return proba, tops

    @staticmethod
    def explain(top_features):
        return [FEATURE_LABELS.get(f, f) for f, _ in top_features]

    # ------------------------------------------------------------------ tasks
    def task_frame(self, tasks: list, weather_severity: float = 0.0) -> pd.DataFrame:
        recs = []
        for t in tasks:
            op = self.operators.get(t["operator_id"], {})
            start_h = t.get("start_h")
            if start_h is None:
                ts = pd.Timestamp(t["assigned_start"])
                start_h = ts.hour + ts.minute / 60 - 6
            recs.append(dict(
                task_type=t["task_type"], machine_type=self.machine_type.get(t["machine_id"], "excavator"),
                archetype=op.get("behavioral_archetype", "smooth_experienced"),
                weather=t.get("weather") or "clear",
                estimated_time_min=t["estimated_time_min"], machine_age_yrs=t["machine_age_yrs"],
                tenure_months=op.get("tenure_months", 24), start_h=start_h,
            ))
        return pd.DataFrame(recs)

    def predict_tasks(self, tasks: list):
        """Adds predicted_min, pred_lo, pred_hi, delay_prob (P(actual > estimate)) to each task dict."""
        if not tasks:
            return []
        X = self.task_frame(tasks)
        q = {k: m.predict(X) for k, m in self.task_models.items()}
        out = []
        for i, t in enumerate(tasks):
            mid, lo, hi = float(q["q50"][i]), float(q["q10"][i]), float(q["q90"][i])
            lo, hi = min(lo, mid), max(hi, mid)
            out.append({**t, "predicted_min": round(mid, 1), "pred_lo": round(lo, 1), "pred_hi": round(hi, 1),
                        "delay_prob": round(delay_probability(mid, lo, hi, t["estimated_time_min"], t.get("elapsed_min", 0.0)), 3)})
        return out


def delay_probability(mid, lo, hi, estimate, elapsed=0.0, material=True):
    """
    P(task overruns its estimate materially | it has already run `elapsed` minutes).
    "Materially" = by more than max(10 min, 20% of the estimate): small slips are
    normal and are nobody's business in the cab.
    The 10/50/90 quantiles are turned into a normal approximation
    (sigma = (q90 - q10) / 2.563), then conditioned on survival past `elapsed`.
    """
    if material:
        estimate = estimate + max(10.0, 0.2 * estimate)
    sigma = max(1.0, (hi - lo) / 2.563)
    cdf = lambda v: 0.5 * (1 + math.erf((v - mid) / (sigma * math.sqrt(2))))
    surv_est = 1 - cdf(estimate)
    surv_el = 1 - cdf(elapsed) if elapsed > 0 else 1.0
    if elapsed >= estimate:
        return 1.0
    return float(np.clip(surv_est / max(surv_el, 1e-6), 0, 1))
