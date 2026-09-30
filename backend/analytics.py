"""
Supervisor analytics + training-gap recommendations + the foreman trade-off.
Pure read-side functions over SQLite; used by the REST API and to ground the
Gemini agents in real numbers.
"""
from datetime import date

import numpy as np

from .db import rows

# Families of messages -> the training module that addresses them.
TRAINING_MODULES = {
    "proximity": {"title": "Working around people: crew zones & blind spots", "minutes": 15,
                  "keys": ("T5_PROXIMITY", "T4_HOTZONE")},
    "controls": {"title": "Smooth controls: reducing jerk & over-correction", "minutes": 20,
                 "keys": ("T4_SMOOTH",)},
    "seatbelt": {"title": "Seatbelt & three-point contact refresher", "minutes": 5,
                 "keys": ("T5_SEATBELT", "T4_SEATBELT")},
    "slope": {"title": "Slope stability & load positioning", "minutes": 15,
              "keys": ("T5_SLOPE", "T4_SLOPE")},
    "fatigue": {"title": "Fatigue awareness & micro-breaks", "minutes": 10,
                "keys": ("T4_FATIGUE",)},
    "visibility": {"title": "Operating in rain, fog & dust", "minutes": 10,
                   "keys": ("T4_VISIBILITY",)},
    "checklist": {"title": "Pre-shift walkaround inspection done right", "minutes": 10,
                  "keys": ("T2_CHECKLIST",)},
}


def summary(conn):
    k = conn.execute("""SELECT
        SUM(tier=5), SUM(tier=4), SUM(tier=4 AND surfaced_to_operator=1), SUM(tier=3), SUM(tier=2), SUM(tier=1), COUNT(*)
        FROM event_log""").fetchone()
    fb = conn.execute("""SELECT AVG(effectiveness_label='effective'), AVG(why_drawer_opened), AVG(ack_response='acknowledged'),
                         AVG(reaction_latency_s) FROM feedback_log""").fetchone()
    mism = conn.execute("SELECT SUM(mismatch_flag), COUNT(*) FROM checklist_log").fetchone()
    unsafe = conn.execute("SELECT COUNT(*) FROM machine_telemetry WHERE unsafe_event_type IS NOT NULL").fetchone()[0]
    ticks = conn.execute("SELECT COUNT(*) FROM machine_telemetry").fetchone()[0]
    return {
        "tier5": k[0] or 0, "tier4": k[1] or 0, "tier4_surfaced": k[2] or 0, "tier3": k[3] or 0,
        "tier2": k[4] or 0, "tier1": k[5] or 0, "events_total": k[6] or 0,
        "effective_rate": round(fb[0] or 0, 3), "why_open_rate": round(fb[1] or 0, 3),
        "ack_rate": round(fb[2] or 0, 3), "avg_reaction_s": round(fb[3] or 0, 1),
        "checklist_mismatches": mism[0] or 0, "checklist_fields": mism[1] or 0,
        "unsafe_events_ground_truth": unsafe, "telemetry_ticks": ticks,
        # How much ARC compressed: telemetry ticks evaluated vs. things actually put in front of an operator.
        "surfaced_per_1000_ticks": round(1000 * ((k[0] or 0) + (k[2] or 0)) / max(ticks, 1), 2),
        "early_warning": early_warning(conn),
    }


def early_warning(conn):
    """Share of real unsafe events preceded by a surfaced tier-4/5 message in the prior 15 min, and median lead time."""
    ev = rows(conn, "SELECT timestamp, machine_id FROM machine_telemetry WHERE unsafe_event_type IS NOT NULL")
    warned, leads = 0, []
    for e in ev:
        # ISO timestamps compare correctly as strings.
        r = conn.execute("""SELECT MIN(timestamp) FROM event_log WHERE machine_id=? AND tier>=4 AND surfaced_to_operator=1
                            AND message_key != 'T4_TASK_DELAY' AND timestamp <= ? AND timestamp >= ?""",
                         (e["machine_id"], e["timestamp"], _minus(e["timestamp"], 15))).fetchone()[0]
        if r:
            warned += 1
            leads.append((_sec(e["timestamp"]) - _sec(r)) / 60)
    return {"unsafe_events": len(ev), "warned": warned, "coverage": round(warned / max(len(ev), 1), 3),
            "median_lead_min": round(float(np.median(leads)), 1) if leads else None}


def _sec(ts):
    from datetime import datetime
    return datetime.fromisoformat(ts).timestamp()


def _minus(ts, minutes):
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(ts) - timedelta(minutes=minutes)).isoformat()


def trends(conn):
    per_shift = rows(conn, """SELECT e.shift_id, s.date,
            SUM(tier=5) AS tier5, SUM(tier=4 AND surfaced_to_operator=1) AS tier4_surfaced,
            SUM(tier=4 AND surfaced_to_operator=0) AS tier4_held, SUM(tier=3) AS tier3, SUM(tier=2) AS tier2, SUM(tier=1) AS tier1
            FROM event_log e JOIN shift s USING(shift_id) GROUP BY e.shift_id ORDER BY e.shift_id""")
    unsafe = {r["shift_id"]: r["n"] for r in rows(conn, "SELECT shift_id, COUNT(*) n FROM machine_telemetry WHERE unsafe_event_type IS NOT NULL GROUP BY shift_id")}
    eff = {r["shift_id"]: r for r in rows(conn, """SELECT e.shift_id, AVG(f.effectiveness_label='effective') eff, AVG(f.reaction_latency_s) lat
            FROM feedback_log f JOIN event_log e USING(event_id) GROUP BY e.shift_id""")}
    for r in per_shift:
        r["unsafe_events"] = unsafe.get(r["shift_id"], 0)
        r["effective_rate"] = round(eff.get(r["shift_id"], {}).get("eff") or 0, 3)
        r["avg_reaction_s"] = round(eff.get(r["shift_id"], {}).get("lat") or 0, 1)
    by_hour = rows(conn, """SELECT CAST((julianday(e.timestamp) - julianday(s.start_ts)) * 24 AS INT) AS hour,
            SUM(tier>=4) AS alerts, AVG(risk_score_at_trigger) AS avg_risk
            FROM event_log e JOIN shift s USING(shift_id) WHERE tier>=3 GROUP BY hour ORDER BY hour""")
    by_key = rows(conn, """SELECT message_key, tier, COUNT(*) n, SUM(surfaced_to_operator) surfaced FROM event_log
                           WHERE message_key IS NOT NULL GROUP BY message_key ORDER BY tier DESC, n DESC""")
    return {"per_shift": per_shift, "by_hour": [r for r in by_hour if r["hour"] is not None and 0 <= r["hour"] < 8], "by_message": by_key}


def operator_summary(conn):
    ops = rows(conn, """SELECT p.*, c.soft_threshold, c.ambient_z, c.channel, c.trust_score, c.last_reason
                        FROM operator_profile p LEFT JOIN operator_calibration c USING(operator_id) ORDER BY operator_id""")
    fb = {r["operator_id"]: r for r in rows(conn, """
        SELECT e.operator_id, COUNT(*) n,
               AVG(f.effectiveness_label='effective') eff, AVG(f.effectiveness_label='ignored') ignored,
               AVG(f.why_drawer_opened) why, AVG(f.ack_response='acknowledged') ack,
               AVG(f.reaction_latency_s) react, AVG(f.ack_latency_s) ack_lat,
               AVG(f.ack_response='acknowledged' AND f.corrective_action_taken=0) ack_no_action
        FROM feedback_log f JOIN event_log e USING(event_id) GROUP BY e.operator_id""")}
    ev = {r["operator_id"]: r for r in rows(conn, """SELECT operator_id, SUM(tier=5) t5, SUM(tier=4) t4,
            SUM(tier=4 AND surfaced_to_operator=1) t4s, COUNT(DISTINCT shift_id) shifts FROM event_log GROUP BY operator_id""")}
    out = []
    for o in ops:
        f, e = fb.get(o["operator_id"], {}), ev.get(o["operator_id"], {})
        comp = comprehension_label(f)
        out.append({**o, "feedback_n": f.get("n", 0),
                    "effective_rate": round(f.get("eff") or 0, 3), "ignored_rate": round(f.get("ignored") or 0, 3),
                    "why_rate": round(f.get("why") or 0, 3), "ack_rate": round(f.get("ack") or 0, 3),
                    "avg_reaction_s": round(f.get("react") or 0, 1), "avg_ack_s": round(f.get("ack_lat") or 0, 1),
                    "ack_without_action_rate": round(f.get("ack_no_action") or 0, 3),
                    "comprehension_signal": comp,
                    "tier5": e.get("t5", 0), "tier4": e.get("t4", 0), "tier4_surfaced": e.get("t4s", 0), "shifts": e.get("shifts", 0),
                    "training": training_recommendations(conn, o["operator_id"])})
    return out


def comprehension_label(f):
    """Loop-2 read-out in plain words for the supervisor."""
    if not f or not f.get("n"):
        return "not enough data"
    if (f.get("ignored") or 0) > 0.35 or (f.get("ack") or 1) < 0.6:
        return "tuning messages out"
    if (f.get("ack_no_action") or 0) > 0.25:
        return "acknowledges but doesn't act — check understanding"
    if (f.get("why") or 0) > 0.5:
        return "wants more context (opens 'why' often)"
    return "understands and acts"


def training_recommendations(conn, operator_id, top_n=3):
    """Rank training modules by how much more often this operator triggers that family than the fleet (per shift)."""
    shifts = {r["operator_id"]: r["n"] for r in rows(conn, "SELECT operator_id, COUNT(DISTINCT shift_id) n FROM event_log GROUP BY operator_id")}
    total_shifts = max(1, sum(shifts.values()))
    my_shifts = max(1, shifts.get(operator_id, 1))
    recs = []
    for key, mod in TRAINING_MODULES.items():
        ph = ",".join("?" * len(mod["keys"]))
        mine = conn.execute(f"SELECT COUNT(*) FROM event_log WHERE operator_id=? AND message_key IN ({ph})", (operator_id, *mod["keys"])).fetchone()[0]
        fleet = conn.execute(f"SELECT COUNT(*) FROM event_log WHERE message_key IN ({ph})", mod["keys"]).fetchone()[0]
        my_rate, fleet_rate = mine / my_shifts, fleet / total_shifts
        ratio = my_rate / fleet_rate if fleet_rate else 0
        if mine >= 2 and ratio >= 1.25:
            recs.append({"module": key, "title": mod["title"], "minutes": mod["minutes"], "events": mine,
                         "per_shift": round(my_rate, 2), "fleet_per_shift": round(fleet_rate, 2), "ratio": round(ratio, 2),
                         "why": f"{my_rate:.1f}/shift vs fleet {fleet_rate:.1f}/shift ({ratio:.1f}x)"})
    ignored = conn.execute("""SELECT AVG(f.effectiveness_label IN ('ignored','ineffective')) FROM feedback_log f JOIN event_log e USING(event_id)
                              WHERE e.operator_id=?""", (operator_id,)).fetchone()[0] or 0
    if ignored > 0.4:
        recs.append({"module": "coaching", "title": "1:1 coaching: what ARC's messages mean and why they fire", "minutes": 20,
                     "events": None, "ratio": None, "why": f"{ignored:.0%} of messages ignored or not acted on"})
    return sorted(recs, key=lambda r: -(r["ratio"] or 9))[:top_n]


def calibration_history(conn, operator_id=None):
    if operator_id:
        return rows(conn, "SELECT * FROM calibration_history WHERE operator_id=? ORDER BY timestamp", (operator_id,))
    return rows(conn, "SELECT * FROM calibration_history ORDER BY timestamp")


def checklist(conn, mismatch_only=True):
    q = """SELECT c.*, s.date, p.name FROM checklist_log c JOIN shift s USING(shift_id) JOIN operator_profile p USING(operator_id)"""
    if mismatch_only:
        q += " WHERE mismatch_flag=1"
    return rows(conn, q + " ORDER BY c.shift_id DESC")


# ---------------------------------------------------------------------------
# Foreman trade-off (task screen)
# ---------------------------------------------------------------------------
SHIFT_MIN = 480


def tradeoff(engine, machine_id):
    """
    The task-time model says the plan won't fit. Lay out the options with
    their consequences — including the safety cost of "just go faster", which
    ARC computes with the real risk model rather than asserting it.
    """
    s = engine.sessions.get(machine_id)
    if not s:
        return None
    intel = engine.intel
    elapsed = (s.last_feat or {}).get("shift_elapsed_h", 0) * 60 if s.last_feat else 0
    cur = s.current_task(elapsed)
    tasks = []
    finish = elapsed
    for t in s.tasks:
        status = "done" if cur and t["start_offset_min"] < cur["start_offset_min"] else "in_progress" if t is cur else "upcoming"
        remaining = 0.0
        if status == "in_progress":
            remaining = max(5.0, t["predicted_min"] - (elapsed - t["start_offset_min"]))
        elif status == "upcoming":
            remaining = t["predicted_min"]
        finish += remaining
        tasks.append({k: t.get(k) for k in ("task_id", "task_type", "estimated_time_min", "predicted_min", "pred_lo", "pred_hi", "delay_prob", "start_offset_min")}
                     | {"status": status, "remaining_min": round(remaining, 1)})
    overrun = round(finish - SHIFT_MIN, 1)
    upcoming = [t for t in tasks if t["status"] == "upcoming"]
    remaining_work = sum(t["remaining_min"] for t in tasks)

    options = [{"id": "keep", "label": "Keep the plan", "detail": f"Finish ~{_clock(s, finish)}; last task rolls to next shift" if overrun > 0 else f"Finish ~{_clock(s, finish)}",
                "overrun_min": max(0, overrun), "risk_delta": 0.0, "recommended": False}]

    # Option B: push pace. Quantify with the risk model: rougher, faster inputs.
    pace_needed = overrun / remaining_work if remaining_work and overrun > 0 else 0
    if s.last_feat and pace_needed > 0:
        import pandas as pd
        f = dict(s.last_feat)
        base_risk, _ = intel.risk(pd.DataFrame([f]))
        bump = min(1.0, 2.5 * pace_needed)
        for k in ("jerk_mean_5", "input_rate_mean_5"):
            if f.get(k) is not None:
                f[k] = f[k] * (1 + bump)
        f["jerk_trend"] = (f.get("jerk_trend") or 0) + 0.15 * bump
        f["jerk_z"] = (f.get("jerk_z") or 0) + 3 * bump
        new_risk, _ = intel.risk(pd.DataFrame([f]))
        delta = float(new_risk[0] - base_risk[0])
        pol = engine.policy(s.operator_id)
        options.append({"id": "push", "label": f"Push pace +{pace_needed:.0%}",
                        "detail": f"Risk model: {base_risk[0]:.0%} → {new_risk[0]:.0%} likelihood of an unsafe event in 15 min",
                        "overrun_min": 0, "risk_delta": round(delta, 3), "recommended": False,
                        "blocked": bool(new_risk[0] >= pol.soft_threshold),
                        "blocked_reason": "would cross this operator's safety threshold" if new_risk[0] >= pol.soft_threshold else None})

    # Option C: reassign the last upcoming task to a same-type machine with slack.
    if upcoming and overrun > 0:
        last = upcoming[-1]
        mtype = intel.machine_type[machine_id]
        best = None
        for mid, mt in intel.machine_type.items():
            if mid == machine_id or mt != mtype:
                continue
            other = engine.sessions.get(mid)
            planned = rows(engine.conn, "SELECT SUM(estimated_time_min) s FROM task_queue WHERE machine_id=? AND shift_id=?", (mid, s.shift_id))[0]["s"] or 0
            slack = SHIFT_MIN - planned * 1.1
            if other is None or slack > 0:
                if best is None or slack > best[1]:
                    best = (mid, slack)
        if best:
            options.append({"id": "reassign", "label": f"Move {last['task_type'].replace('_', ' ')} ({last['task_id']}) to {best[0]}",
                            "detail": f"{best[0]} has ~{max(0, best[1]):.0f} min slack; saves ~{last['predicted_min']:.0f} min here",
                            "overrun_min": max(0, round(overrun - last["predicted_min"], 1)), "risk_delta": 0.0,
                            "recommended": False, "target_machine": best[0], "task_id": last["task_id"]})
    # Recommend: the option with the least overrun that doesn't raise risk.
    safe = [o for o in options if o["risk_delta"] <= 0.01 and not o.get("blocked")]
    min(safe, key=lambda o: o["overrun_min"])["recommended"] = True
    return {"machine_id": machine_id, "operator_id": s.operator_id, "elapsed_min": round(elapsed, 1),
            "predicted_finish_min": round(finish, 1), "shift_end_min": SHIFT_MIN, "overrun_min": overrun,
            "tasks": tasks, "options": options}


def _clock(s, minutes_from_shift_start):
    from datetime import timedelta
    return (s.shift_start + timedelta(minutes=minutes_from_shift_start)).strftime("%H:%M")
