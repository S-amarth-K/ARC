"""
The fixed message library.

Nothing the operator hears or reads during active operation is generated live.
Every line below is pre-written, reviewed, and has a matching pre-recorded
audio clip at frontend/public/audio/<KEY>.wav (see scripts/make_audio.ps1).

Rules for writing these (keep to them when editing):
  * one action per line, verb first, <= 10 words
  * tier 5 uses the fixed stop-vocabulary: "STOP." + what + what to do
  * never tell the operator to go faster
"""

MESSAGES = {
    # ---------------- Tier 5: immediate intervention (fixed vocabulary) ----------------
    "T5_PROXIMITY":   {"tier": 5, "icon": "safety", "text": "STOP. Person close to the machine."},
    "T5_SEATBELT":    {"tier": 5, "icon": "safety", "text": "STOP. Fasten your seatbelt."},
    "T5_SLOPE":       {"tier": 5, "icon": "safety", "text": "STOP. Slope too steep. Lower the bucket."},
    "T5_RISK":        {"tier": 5, "icon": "safety", "text": "STOP. Hold position and look around."},

    # ---------------- Tier 4: one plain-language action ----------------
    "T4_HOTZONE":     {"tier": 4, "icon": "safety", "text": "Crew area close. Slow to walking pace."},
    "T4_SEATBELT":    {"tier": 4, "icon": "safety", "text": "Seatbelt is off. Buckle up before moving."},
    "T4_SMOOTH":      {"tier": 4, "icon": "safety", "text": "Ease off the controls. Movements are getting sharp."},
    "T4_FATIGUE":     {"tier": 4, "icon": "safety", "text": "Take a ten-minute break at the next stop."},
    "T4_VISIBILITY":  {"tier": 4, "icon": "safety", "text": "Low visibility. Slow down and use the camera."},
    "T4_SLOPE":       {"tier": 4, "icon": "safety", "text": "Ground is getting steep. Keep the load low."},
    "T4_HYD_HOT":     {"tier": 4, "icon": "health", "text": "Hydraulics running hot. Ease the load for five minutes."},
    "T4_RISK_RISING": {"tier": 4, "icon": "safety", "text": "Risk is rising. Slow down and check around you."},
    "T4_TASK_DELAY":  {"tier": 4, "icon": "pace",   "text": "Task running long. Foreman knows. Keep a safe pace."},

    # ---------------- Tier 3: ambient icon only (no text shown in cab) ----------------
    "T3_SAFETY":      {"tier": 3, "icon": "safety", "text": "Safety indicator amber"},
    "T3_PACE":        {"tier": 3, "icon": "pace",   "text": "Pace indicator amber"},
    "T3_HEALTH":      {"tier": 3, "icon": "health", "text": "Machine-health indicator amber"},

    # ---------------- Tier 2: supervisor log only ----------------
    "T2_CHECKLIST":   {"tier": 2, "icon": None, "text": "Pre-shift checklist entry does not match sensor reading."},
    "T2_EFFICIENCY":  {"tier": 2, "icon": None, "text": "Fuel per unit of work trending above fleet norm."},
    "T2_IDLE":        {"tier": 2, "icon": None, "text": "Idle time trending above this operator's normal."},
    "T2_FOREMAN":     {"tier": 2, "icon": None, "text": "Foreman trade-off decision recorded."},

    # ---------------- Tier 1: automation acted silently (logged, never shown) ----------------
    "T1_AUTO_IDLE":   {"tier": 1, "icon": None, "text": "Auto-idle engaged: engine speed lowered after 5 min idling."},
    "T1_FAN_BOOST":   {"tier": 1, "icon": None, "text": "Hydraulic cooling fan boosted."},
    "T1_CAMERA":      {"tier": 1, "icon": None, "text": "Rear camera view auto-selected for nearby object."},
}

# Operator-facing lines (tiers 4-5) have pre-recorded audio.
AUDIO_KEYS = [k for k, v in MESSAGES.items() if v["tier"] >= 4]


def audio_ref(key):
    return f"/audio/{key}.wav" if key in AUDIO_KEYS else None


# Which tier-4 / tier-5 line to use, chosen from the model's top contributing
# feature. First match wins; order = safety relevance.
FEATURE_TO_T4 = [
    (("prox_min_5", "prox_trend", "prox_events_5", "hot_zone_dist_m"), "T4_HOTZONE"),
    (("belt_off", "belt_off_count_5"), "T4_SEATBELT"),
    (("slope_deg", "slope_max_5", "slope_trend"), "T4_SLOPE"),
    (("jerk_mean_5", "jerk_std_5", "jerk_trend", "jerk_z", "input_rate_mean_5"), "T4_SMOOTH"),
    (("weather_severity", "visibility_log"), "T4_VISIBILITY"),
    (("shift_elapsed_h", "time_since_break_h"), "T4_FATIGUE"),
    (("hyd_temp_C", "hyd_temp_trend"), "T4_HYD_HOT"),
]
T4_TO_T5 = {"T4_HOTZONE": "T5_PROXIMITY", "T4_SEATBELT": "T5_SEATBELT", "T4_SLOPE": "T5_SLOPE"}

# Tier-4 lines that are not about immediate physical safety; these may be
# deferred when the operator's attention budget is low.
NON_SAFETY_T4 = {"T4_TASK_DELAY", "T4_HYD_HOT", "T4_FATIGUE"}


def pick_t4(top_features):
    for f, _ in top_features:
        for feats, key in FEATURE_TO_T4:
            if f in feats:
                return key
    return "T4_RISK_RISING"


def pick_t5(top_features, hard_rule=None):
    if hard_rule:
        return {"proximity": "T5_PROXIMITY", "seatbelt": "T5_SEATBELT", "slope": "T5_SLOPE"}[hard_rule]
    return T4_TO_T5.get(pick_t4(top_features), "T5_RISK")
