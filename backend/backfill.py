"""
Back-fill event_log / feedback_log / calibration history by running the REAL
Arbitration Engine (pipeline.Engine.step) over every historical shift.

    python -m backend.backfill

The historical telemetry was generated without ARC in the loop, so operator
*reactions* can't be read from it. Instead a SimulatedOperator reacts to each
surfaced message according to the operator's comprehension archetype:

  fast_confident       acks quickly, rarely opens "why", usually complies
  cautious_rechecker   acks, opens "why" most of the time, complies (slower)
  low_trust_dismisser  dismisses / ignores often, and gets WORSE over the two
                       weeks (rising latency) — this is what the calibration
                       loop has to notice and respond to.

Calibration runs after every scored event, in chronological order, so the
calibration_history table shows each operator's policy evolving.
"""
from datetime import datetime

import numpy as np

from .calibration import effectiveness_label, init_policies
from .config import SEED
from .db import connect
from .ml.models import Intelligence
from .ml.train import build_all_features, fit_risk
from .pipeline import Engine, _ts

PROFILE = {
    #                        p_ack  p_dismiss  ack_lat(s)   p_why  p_fix  fix_lat(s)   p_back  drift/shift
    "fast_confident":      (0.92, 0.05, (1.2, 3.5), 0.10, 0.82, (10, 45), 0.75, 0.00),
    "cautious_rechecker":  (0.95, 0.02, (3.0, 8.0), 0.65, 0.80, (20, 70), 0.72, 0.00),
    "low_trust_dismisser": (0.45, 0.35, (4.0, 12.0), 0.05, 0.50, (40, 150), 0.50, 0.07),
}


class SimulatedOperator:
    def __init__(self, rng, operators):
        self.rng = rng
        self.operators = operators
        self.shift_index = 0

    def react(self, engine, s, ev, now):
        prof = self.operators[s.operator_id]["comprehension_profile"]
        p_ack, p_dis, ack_lat, p_why, p_fix, fix_lat, p_back, drift = PROFILE[prof]
        r = self.rng
        worse = 1 + drift * self.shift_index            # low-trust operators degrade over time
        t5 = ev["tier"] == 5
        u = r.random()
        if t5:                                          # tier 5 must be acknowledged; almost everyone complies
            ack, p_fix, p_back = "acknowledged", max(p_fix, 0.9), max(p_back, 0.8)
        else:
            pa, pd_ = p_ack / worse, min(1 - p_ack / worse, p_dis * worse)
            ack = "acknowledged" if u < pa else "dismissed" if u < pa + pd_ else "no_response"
        fixed = r.random() < (p_fix / worse if not t5 else p_fix)
        back = fixed and r.random() < p_back
        fb = dict(
            event_id=ev["event_id"],
            reaction_latency_s=round(float(r.uniform(*fix_lat) * worse), 1) if fixed else None,
            corrective_action_taken=int(fixed), metric_returned_to_baseline=int(back),
            why_drawer_opened=int(r.random() < p_why), ack_response=ack,
            effectiveness_label=effectiveness_label(fixed, back, ack),
            ack_latency_s=round(float(r.uniform(*ack_lat) * worse), 1) if ack != "no_response" else None,
        )
        engine.write_feedback(s, fb, now)
        if t5:
            s.gate.disarmed.add(ev["message_key"])   # acknowledged: don't re-fire until the situation clears
            s.gate.active_intervention = None
        else:
            s.gate.active_card = None


def main():
    conn = connect()
    for t in ("event_log", "feedback_log", "signal_snapshot"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('event_log','signal_snapshot')")
    ops = {r["operator_id"]: dict(r) for r in conn.execute("SELECT * FROM operator_profile")}
    shifts = [dict(r) for r in conn.execute("SELECT * FROM shift WHERE status='completed' ORDER BY shift_id")]
    init_policies(conn, list(ops), ts=shifts[0]["start_ts"])

    print("Computing features for all historical shifts...")
    feats, _ = build_all_features(conn, verbose=False)
    intel = Intelligence(conn)
    sim = SimulatedOperator(np.random.default_rng(SEED + 1), ops)
    engine = Engine(conn, intel, source="historical", sim_operator=sim)

    # Out-of-fold scoring: each shift is scored by a model that never saw it,
    # so the back-filled history reflects honest (not memorised) risk scores.
    ids = [sh["shift_id"] for sh in shifts]
    folds = [ids[i::4] for i in range(4)]
    fold_model = {}
    for fold in folds:
        m = fit_risk(feats[~feats.shift_id.isin(fold)])
        fold_model.update({sid: m for sid in fold})

    for k, sh in enumerate(shifts):
        sim.shift_index = k
        fs = feats[feats.shift_id == sh["shift_id"]]
        for mid, g in fs.groupby("machine_id"):
            g = g.sort_values("timestamp")
            proba, tops = intel.risk(g, model=fold_model[sh["shift_id"]])
            s = engine.open_session(mid, g["operator_id"].iloc[0], sh["shift_id"], datetime.fromisoformat(sh["start_ts"]))
            for i, rec in enumerate(g.to_dict("records")):
                engine.step(s, rec, float(proba[i]), tops[i], _ts(rec["timestamp"]))
        conn.commit()
        n = conn.execute("SELECT tier, COUNT(*) FROM event_log WHERE shift_id=? GROUP BY tier", (sh["shift_id"],)).fetchall()
        print(f"  {sh['shift_id']}: events by tier {dict((t, c) for t, c in n)}")

    print("\nFinal per-operator policy:")
    for r in conn.execute("""SELECT c.*, p.comprehension_profile FROM operator_calibration c
                             JOIN operator_profile p USING(operator_id) ORDER BY operator_id"""):
        print(f"  {r['operator_id']} {r['comprehension_profile']:<20} soft={r['soft_threshold']:.2f} "
              f"channel={r['channel']:<12} trust={r['trust_score']}  ({r['last_reason']})")
    tot = dict(conn.execute("SELECT tier, COUNT(*) FROM event_log GROUP BY tier").fetchall())
    surf = conn.execute("SELECT COUNT(*) FROM event_log WHERE surfaced_to_operator=1 AND tier>=4").fetchone()[0]
    print(f"\nTotal events by tier: {tot}; tier-4/5 actually surfaced to operators: {surf}")
    conn.close()


if __name__ == "__main__":
    main()
