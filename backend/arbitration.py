"""
Phase 3 — The Arbitration Engine.

Every tick, for every machine, ALL intelligence (risk model, personal
baseline, task model, checklist cross-check, machine health) is funnelled
through this one module. It answers a single question:

    "What, if anything, should this operator see right now?"

It is deliberately a readable rules + ML hybrid:
  * the ML models produce numbers (risk score, z-scores, delay probability)
  * `route()` turns those numbers into a tier with five plain if-statements
  * `Gate` enforces "one thing at a time" (cooldowns, attention budget)

Tiers
  5  Immediate intervention  (fixed vocabulary + audio, must be acknowledged)
  4  One plain-language action card (why-drawer collapsed by default)
  3  Ambient icon turns amber (no words)
  2  Supervisor log only (operator never sees it)
  1  Act silently / nothing to say (automation actions are still logged)

Invariant: everything with tier >= 2, and every silent automation action,
is written to event_log — including things the Gate chose NOT to surface.
Only *surfacing* is filtered, never logging.
"""
from dataclasses import dataclass, field

from .config import HARD_SAFETY_THRESHOLD, TASK_DELAY_CAB_COOLDOWN_S, TASK_DELAY_THRESHOLD
from .messages import MESSAGES, NON_SAFETY_T4, pick_t4, pick_t5


@dataclass
class Policy:
    """Per-operator settings. soft_threshold/ambient_z/channel are tuned by the feedback loop."""
    soft_threshold: float
    ambient_z: float
    channel: str = "visual"          # visual | visual_audio  (tier 5 is always audio)


@dataclass
class Signals:
    """Everything the router looks at for one machine at one tick."""
    risk_score: float
    top_features: list
    deviation_z: float               # max personal-baseline deviation (jerk / cycle / idle)
    deviation_metric: str = "jerk"   # which baseline metric deviates most
    task_delay_prob: float = 0.0
    checklist_mismatch: bool = False
    minor_inefficiency_trend: bool = False
    hard_rule: str | None = None     # deterministic safety rule tripped (proximity/seatbelt/slope)
    attention_budget: float = 100.0


@dataclass
class Decision:
    tier: int
    message_key: str | None
    reason: str
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Deterministic hard rules — these can never be learned, tuned or calibrated away.
# ---------------------------------------------------------------------------
def hard_rule(feat: dict) -> str | None:
    moving = feat.get("moving", 0) == 1
    if moving and feat.get("proximity_distance_m", 50) < 3.0:
        return "proximity"
    if moving and feat.get("belt_off", 0) == 1 and feat.get("speed_kmh", 0) > 3:
        return "seatbelt"
    if moving and feat.get("slope_deg", 0) > 18:
        return "slope"
    return None


# ---------------------------------------------------------------------------
# THE ROUTER. This is the function to put on a slide.
# ---------------------------------------------------------------------------
def route(s: Signals, p: Policy) -> Decision:
    if s.hard_rule or s.risk_score >= HARD_SAFETY_THRESHOLD:           # hard floor, never calibrated away
        key = pick_t5(s.top_features, s.hard_rule)
        why = f"hard rule: {s.hard_rule}" if s.hard_rule else f"risk {s.risk_score:.2f} >= hard floor {HARD_SAFETY_THRESHOLD}"
        return Decision(5, key, why)

    if s.risk_score >= p.soft_threshold:                                # personalised soft threshold
        return Decision(4, pick_t4(s.top_features), f"risk {s.risk_score:.2f} >= operator soft threshold {p.soft_threshold:.2f}")
    if s.task_delay_prob > TASK_DELAY_THRESHOLD:
        return Decision(4, "T4_TASK_DELAY", f"task delay probability {s.task_delay_prob:.2f} > {TASK_DELAY_THRESHOLD}")

    if s.checklist_mismatch:                                            # supervisor only
        return Decision(2, "T2_CHECKLIST", "checklist entry vs sensor mismatch")
    if s.minor_inefficiency_trend:
        return Decision(2, "T2_EFFICIENCY", "effort-to-output above fleet norm for 10+ min")

    if s.deviation_z > p.ambient_z:                                     # ambient icon only
        key = "T3_SAFETY" if s.deviation_metric == "jerk" else "T3_PACE"
        return Decision(3, key, f"{s.deviation_metric} deviation z {s.deviation_z:.1f} > ambient threshold {p.ambient_z:.1f}")

    return Decision(1, None, "nominal")


# ---------------------------------------------------------------------------
# Ambient strip: three colour-only icons, recomputed every tick.
# ---------------------------------------------------------------------------
def ambient_state(feat: dict, risk: float, task_delay_prob: float, p: Policy) -> dict:
    jz, cz = feat.get("jerk_z") or 0, feat.get("cycle_z") or 0
    safety = "red" if risk >= p.soft_threshold else "amber" if (risk >= 0.6 * p.soft_threshold or jz > p.ambient_z) else "green"
    pace = "amber" if (cz > p.ambient_z or task_delay_prob > 0.5) else "green"   # pace never goes red: we don't hurry people
    hyd, cool = feat.get("hyd_temp_C", 60), feat.get("coolant_temp_C", 85)
    health = "red" if hyd > 98 else "amber" if (hyd > 88 or cool > 98) else "green"
    return {"safety": safety, "pace": pace, "health": health}


# ---------------------------------------------------------------------------
# Tier-1 automation: things the machine just does, silently. Always logged.
# ---------------------------------------------------------------------------
def silent_actions(feat: dict, idle_streak_ticks: int) -> list[str]:
    acts = []
    if idle_streak_ticks >= 10:                                          # 5 min idling
        acts.append("T1_AUTO_IDLE")
    if feat.get("hyd_temp_C", 0) > 88:
        acts.append("T1_FAN_BOOST")
    if feat.get("proximity_direction") == "rear" and 3 <= feat.get("proximity_distance_m", 50) < 15:
        acts.append("T1_CAMERA")
    return acts


# ---------------------------------------------------------------------------
# The Gate: "operator sees at most one thing at a time".
# ---------------------------------------------------------------------------
class Gate:
    """
    Stateful per-machine filter between the router and the cab.
    Returns (log_it, surface_it, note). log_it=False only for exact repeats
    inside the per-message cooldown (those are de-duplicated, not discarded:
    the first occurrence is in the log and the condition is still visible in
    the telemetry snapshot).
    """

    def __init__(self, tier4_min_gap_s: float, cooldown_s: float):
        self.tier4_min_gap_s = tier4_min_gap_s
        self.cooldown_s = cooldown_s
        self.last_by_key: dict[str, float] = {}
        self.last_surfaced_t4 = -1e18
        self.active_card: dict | None = None          # tier-4 card on screen
        self.active_intervention: dict | None = None  # tier-5 awaiting acknowledgement
        self.disarmed: set[str] = set()                # tier-5 keys already acknowledged; re-arm once risk clears

    def rearm(self):
        """Called when the router drops back below tier 4: the same stop may fire again next time."""
        self.disarmed.clear()

    def cooling(self, key, now, cooldown=None):
        return now - self.last_by_key.get(key, -1e18) < (cooldown or self.cooldown_s)

    def admit(self, d: Decision, now: float, attention_budget: float):
        key = d.message_key
        if d.tier == 5:
            # Re-fire the same stop only after 60 s, and never stack on an un-acked one of the same kind.
            if self.active_intervention and self.active_intervention["message_key"] == key:
                return False, False, "already on screen"
            if key in self.disarmed:
                return False, False, "held: acknowledged, waiting for the situation to clear"
            if self.cooling(key, now, 60):
                return False, False, "cooldown"
            self.last_by_key[key] = now
            return True, True, "tier 5 always surfaces (pre-empts any card)"

        if d.tier == 4:
            if key == "T4_TASK_DELAY" and self.cooling(key, now, TASK_DELAY_CAB_COOLDOWN_S):
                # Each overrunning task is a distinct fact for the foreman: log it,
                # but the operator only hears "foreman knows" once every few hours.
                return True, False, "held: operator already told; foreman informed"
            if self.cooling(key, now):
                return False, False, "cooldown"
            self.last_by_key[key] = now
            if self.active_intervention:
                return True, False, "held: tier-5 intervention on screen"
            if self.active_card:
                return True, False, "held: another card already on screen"
            if now - self.last_surfaced_t4 < self.tier4_min_gap_s:
                return True, False, "held: min gap between cards"
            if key in NON_SAFETY_T4 and attention_budget < 40:
                return True, False, f"deferred: attention budget {attention_budget:.0f}/100"
            self.last_surfaced_t4 = now
            return True, True, "surfaced"

        # Tiers 1-3 are edge-triggered with the standard cooldown.
        if key and self.cooling(key, now):
            return False, False, "cooldown"
        if key:
            self.last_by_key[key] = now
        return True, d.tier == 3, "ambient" if d.tier == 3 else "logged only"


def message(key):
    return MESSAGES.get(key, {}).get("text") if key else None
