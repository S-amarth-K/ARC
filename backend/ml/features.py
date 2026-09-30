"""
Phase 2 — Feature engineering.

ONE function, `compute_features`, turns raw telemetry into model features.
It is used both offline (training / back-fill, over whole shifts) and online
(the live pipeline runs it over a rolling 40-tick buffer per machine), so the
model never sees features computed differently in training vs. serving.

Window sizes are in ticks (1 tick = 30 s): W5 = 5 min, W10 = 10 min.
"""
import numpy as np
import pandas as pd

from ..site_model import from_latlon, nearest_hot_zone

W5, W10 = 10, 20
MOVING_STATES = ("working", "traveling")

# Features the risk-trajectory classifier is trained on (order matters).
RISK_FEATURES = [
    "shift_elapsed_h", "time_since_break_h",
    "jerk_mean_5", "jerk_std_5", "jerk_trend", "jerk_z",
    "input_rate_mean_5", "cycle_z", "idle_ratio_10", "idle_z",
    "prox_min_5", "prox_trend", "prox_events_5", "hot_zone_dist_m",
    "belt_off", "belt_off_count_5", "moving", "speed_kmh",
    "slope_deg", "slope_max_5", "slope_trend",
    "hyd_temp_C", "hyd_temp_trend",
    "weather_severity", "visibility_log", "tenure_months",
]

# Plain-language names used for "why" explanations — operators never see
# raw feature names.
FEATURE_LABELS = {
    "shift_elapsed_h": "hours into the shift",
    "time_since_break_h": "time since your last break",
    "jerk_mean_5": "sharp control movements",
    "jerk_std_5": "uneven control movements",
    "jerk_trend": "control movements getting sharper",
    "jerk_z": "controls rougher than your normal",
    "input_rate_mean_5": "very fast control inputs",
    "cycle_z": "cycle time off your normal",
    "idle_ratio_10": "a lot of idling",
    "idle_z": "more idling than your normal",
    "prox_min_5": "someone or something close to the machine",
    "prox_trend": "an object getting closer",
    "prox_events_5": "repeated proximity alerts",
    "hot_zone_dist_m": "working near a crew/traffic zone",
    "belt_off": "seatbelt unfastened",
    "belt_off_count_5": "seatbelt off for a while",
    "moving": "machine in motion",
    "speed_kmh": "travel speed",
    "slope_deg": "steep ground",
    "slope_max_5": "steep ground nearby",
    "slope_trend": "ground getting steeper",
    "hyd_temp_C": "hydraulic temperature",
    "hyd_temp_trend": "hydraulics heating up",
    "weather_severity": "bad weather",
    "visibility_log": "low visibility",
    "tenure_months": "experience level",
}

BASELINE_METRICS = ("jerk", "cycle_rel", "idle_ratio")


def _hot_zone_dist(lat, lon):
    x, y = from_latlon(lat, lon)
    return nearest_hot_zone(x, y)[1]


def compute_features(tel: pd.DataFrame, baselines: dict, fleet_cycle_median: dict,
                     tenure: dict, machine_type: dict) -> pd.DataFrame:
    """
    tel: telemetry rows for ONE machine in ONE shift, sorted by time. Must include
         `shift_elapsed_h` (hours since shift start) in addition to raw fields.
    baselines: {operator_id: {metric: (mean, std)}} personal baselines.
    fleet_cycle_median: {machine_type: median cycle seconds} to make cycle time
         comparable across machine types before personalising it.
    """
    df = tel.copy()
    op = df["operator_id"].iloc[-1]
    mtype = machine_type[df["machine_id"].iloc[-1]]
    base = baselines.get(op, {})

    h = df["shift_elapsed_h"]
    df["time_since_break_h"] = np.where(h >= 4.5, h - 4.5, h)

    working = df["activity_state"] == "working"
    jerk_work = df["joystick_jerk"].where(working)
    df["jerk_mean_5"] = jerk_work.rolling(W5, min_periods=2).mean()
    df["jerk_std_5"] = jerk_work.rolling(W5, min_periods=3).std()
    df["jerk_trend"] = df["jerk_mean_5"] - jerk_work.rolling(W10, min_periods=4).mean().shift(W5)
    df["input_rate_mean_5"] = df["joystick_input_rate"].where(working).rolling(W5, min_periods=2).mean()

    # Cycle time: seconds per load cycle while working, relative to the fleet
    # median for that machine type (so an excavator and a truck are comparable).
    cyc = df["load_cycles"].diff(W10)
    work_s = working.rolling(W10, min_periods=1).sum() * 30
    cycle_s = (work_s / cyc.replace(0, np.nan)).where(work_s > 120)
    df["cycle_rel"] = cycle_s / fleet_cycle_median.get(mtype, 60.0)

    df["idle_ratio_10"] = (df["activity_state"] == "idling").rolling(W10, min_periods=1).mean()

    prox = df["proximity_distance_m"]
    df["prox_min_5"] = prox.rolling(W5, min_periods=1).min()
    df["prox_trend"] = prox.rolling(3, min_periods=1).mean() - prox.shift(5).rolling(3, min_periods=1).mean()
    df["prox_events_5"] = (prox < 50).rolling(W5, min_periods=1).sum()
    df["hot_zone_dist_m"] = [_hot_zone_dist(a, b) for a, b in zip(df["gps_lat"], df["gps_lon"])]

    df["belt_off"] = 1 - df["seatbelt_status"]
    df["belt_off_count_5"] = df["belt_off"].rolling(W5, min_periods=1).sum()
    df["moving"] = df["activity_state"].isin(MOVING_STATES).astype(int)
    df["speed_kmh"] = df["ground_speed_kmh"]

    df["slope_deg"] = df["terrain_slope_deg"]
    df["slope_max_5"] = df["terrain_slope_deg"].rolling(W5, min_periods=1).max()
    df["slope_trend"] = df["terrain_slope_deg"] - df["terrain_slope_deg"].shift(W5)

    df["hyd_temp_C"] = df["hydraulic_temp_C"]
    df["hyd_temp_trend"] = df["hydraulic_temp_C"] - df["hydraulic_temp_C"].shift(W10)

    df["visibility_log"] = np.log10(df["visibility"].clip(lower=10))
    df["tenure_months"] = tenure.get(op, 24)

    # ---- personalised deviation z-scores (Tier 3 input) ----
    def z(series, metric):
        m, s = base.get(metric, (np.nan, np.nan))
        return (series - m) / (s if s and s > 1e-6 else 1.0)

    df["jerk_z"] = z(df["jerk_mean_5"], "jerk")
    df["cycle_z"] = z(df["cycle_rel"], "cycle_rel")
    df["idle_z"] = z(df["idle_ratio_10"], "idle_ratio")

    # ---- derived operating indices (Phase 2 "derived columns") ----
    # Effort-to-output: fuel burned per unit of work, relative to fleet norm (1.0 = typical).
    fuel_10 = df["fuel_used_L"].diff(W10)
    work_units = (cyc.clip(lower=0) * df["payload_kg"].rolling(W10, min_periods=1).mean().clip(lower=1))
    df["effort_to_output"] = (fuel_10 / work_units.replace(0, np.nan)) / _EFFORT_NORM.get(mtype, 1.0)

    # Attention budget (0-100): how much spare attention the operator likely has.
    # Low budget => arbitration defers anything non-safety.
    closeness = ((25 - df["prox_min_5"].clip(0, 25)) / 25)
    fatigue = (df["time_since_break_h"] / 4).clip(0, 1)
    rough = (df["jerk_z"].fillna(0).clip(0, 4) / 4)
    df["attention_budget"] = (100 - 30 * df["weather_severity"] - 30 * closeness - 20 * fatigue - 20 * rough).clip(0, 100)

    return df


# Median fuel-per-(cycle*kg) per machine type; filled in by train.py and
# loaded by models.py. Kept module-level so compute_features stays a pure fn.
_EFFORT_NORM: dict = {}


def set_effort_norm(d):
    _EFFORT_NORM.clear()
    _EFFORT_NORM.update(d)


def add_shift_elapsed(tel: pd.DataFrame, shift_start: dict) -> pd.DataFrame:
    ts = pd.to_datetime(tel["timestamp"])
    starts = pd.to_datetime(tel["shift_id"].map(shift_start))
    tel = tel.copy()
    tel["shift_elapsed_h"] = (ts - starts).dt.total_seconds() / 3600
    return tel


def label_future_unsafe(df: pd.DataFrame, min_ahead: int, max_ahead: int) -> pd.Series:
    """1 if an unsafe event occurs in ticks (t+min_ahead, t+max_ahead]. Per machine-shift frame."""
    ev = df["unsafe_event_type"].notna().astype(int).to_numpy()
    n = len(ev)
    csum = np.concatenate([[0], np.cumsum(ev)])
    idx = np.arange(n)
    lo = np.minimum(idx + min_ahead + 1, n)
    hi = np.minimum(idx + max_ahead + 1, n)
    return pd.Series((csum[hi] - csum[lo] > 0).astype(int), index=df.index)
