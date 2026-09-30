"""
Phase 7 — Feedback loop + per-operator calibration.

Two loops are scored for every tier-4/5 event (see pipeline.FeedbackWatcher):
  Loop 1 "did it work?"          reaction_latency_s, corrective_action_taken,
                                 metric_returned_to_baseline
  Loop 2 "did they understand?"  why_drawer_opened, ack_response (+ ack latency)

`update_policy()` turns the recent history of those signals into a new
Policy for that operator.

Architecturally this is a contextual bandit (context = operator + situation,
arm = threshold/channel, reward = effectiveness). For the demo it is
implemented as a transparent rule-based policy update, so every change has a
human-readable reason stored in calibration_history.

The hard safety floor (tier 5) is NOT touched here. Ever.
"""
from datetime import datetime

from .arbitration import Policy
from .config import DEFAULT_AMBIENT_Z, DEFAULT_SOFT_THRESHOLD, SOFT_MAX, SOFT_MIN
from .db import rows

WINDOW = 12            # look at the operator's last 12 scored tier-4 events
MIN_EVENTS = 5         # don't adapt on thin evidence
STEP_TRUST = 0.02      # earlier/quieter nudges for operators who act on them
STEP_BACKOFF = 0.03    # fewer, louder nudges for operators who ignore them


def effectiveness_label(corrective, returned, ack):
    if corrective and returned:
        return "effective"
    if corrective or returned:
        return "partial"
    if ack == "acknowledged":
        return "ineffective"
    return "ignored"


def get_policy(conn, operator_id) -> Policy:
    r = conn.execute("SELECT * FROM operator_calibration WHERE operator_id=?", (operator_id,)).fetchone()
    if not r:
        return Policy(DEFAULT_SOFT_THRESHOLD, DEFAULT_AMBIENT_Z, "visual")
    return Policy(r["soft_threshold"], r["ambient_z"], r["channel"])


def save_policy(conn, operator_id, pol: Policy, trust, reason, ts=None):
    ts = ts or datetime.now().isoformat(timespec="seconds")
    conn.execute("""INSERT INTO operator_calibration VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(operator_id) DO UPDATE SET soft_threshold=excluded.soft_threshold,
                    ambient_z=excluded.ambient_z, channel=excluded.channel, trust_score=excluded.trust_score,
                    updated_at=excluded.updated_at, last_reason=excluded.last_reason""",
                 (operator_id, pol.soft_threshold, pol.ambient_z, pol.channel, trust, ts, reason))
    conn.execute("INSERT INTO calibration_history VALUES (?,?,?,?,?,?,?)",
                 (operator_id, ts, pol.soft_threshold, pol.ambient_z, pol.channel, trust, reason))


def init_policies(conn, operator_ids, ts=None):
    conn.execute("DELETE FROM operator_calibration")
    conn.execute("DELETE FROM calibration_history")
    for op in operator_ids:
        save_policy(conn, op, Policy(DEFAULT_SOFT_THRESHOLD, DEFAULT_AMBIENT_Z, "visual"), 0.5, "initial default", ts)
    conn.commit()


def recent_feedback(conn, operator_id, n=WINDOW):
    return rows(conn, """SELECT f.*, e.timestamp, e.tier FROM feedback_log f JOIN event_log e USING(event_id)
                         WHERE e.operator_id=? AND e.tier=4 ORDER BY e.timestamp DESC LIMIT ?""", (operator_id, n))


def update_policy(conn, operator_id, ts=None):
    """Recompute one operator's policy from their recent feedback. Returns (policy, reason) or None if unchanged."""
    fb = recent_feedback(conn, operator_id)
    if len(fb) < MIN_EVENTS:
        return None
    pol = get_policy(conn, operator_id)
    n = len(fb)
    eff_rate = sum(f["effectiveness_label"] == "effective" for f in fb) / n
    ignore_rate = sum(f["effectiveness_label"] == "ignored" or f["ack_response"] in ("dismissed", "no_response") for f in fb) / n
    why_rate = sum(bool(f["why_drawer_opened"]) for f in fb) / n
    # Latency trend: newest half vs oldest half (fb is newest-first).
    lat = [f["reaction_latency_s"] for f in fb if f["reaction_latency_s"] is not None]
    half = len(lat) // 2
    rising = half >= 3 and (sum(lat[:half]) / half) > 1.5 * (sum(lat[half:]) / (len(lat) - half))

    trust = round(0.6 * eff_rate + 0.4 * (1 - ignore_rate), 3)
    new = Policy(pol.soft_threshold, pol.ambient_z, pol.channel)
    reasons = []

    if eff_rate >= 0.6 and ignore_rate <= 0.15:
        # Operator acts on nudges -> trust them with earlier, quieter nudges.
        new.soft_threshold = pol.soft_threshold - STEP_TRUST
        new.channel = "visual"
        reasons.append(f"effective {eff_rate:.0%}, ignored {ignore_rate:.0%}: earlier + quieter nudges")
    elif ignore_rate >= 0.35 or (rising and eff_rate < 0.6):
        # Operator is tuning ARC out -> speak less often, but louder (escalate channel).
        new.soft_threshold = pol.soft_threshold + STEP_BACKOFF
        new.channel = "visual_audio"
        why = f"ignored {ignore_rate:.0%}" + (", reaction latency rising" if rising else "")
        reasons.append(f"{why}: fewer nudges, escalate to audio")

    # Loop 2: an operator who keeps opening "why" is telling us the one-liner is not enough.
    if why_rate >= 0.5:
        new.ambient_z = max(1.5, pol.ambient_z - 0.1)   # earlier ambient hints give more context before a card
        reasons.append(f"opens 'why' {why_rate:.0%} of the time: earlier ambient hints")

    new.soft_threshold = round(min(SOFT_MAX, max(SOFT_MIN, new.soft_threshold)), 3)
    if (new.soft_threshold, new.ambient_z, new.channel) == (pol.soft_threshold, pol.ambient_z, pol.channel):
        return None
    reason = "; ".join(reasons)
    save_policy(conn, operator_id, new, trust, reason, ts)
    conn.commit()
    return new, reason
