"""
Static description of the synthetic job site: machines, operators, hot zones
and terrain. Shared by the dataset generator, the live pipeline and the demo
so everyone agrees on where the dangerous places are.
"""
import math

# Site origin (a fictional quarry near Peoria, IL). Local coordinates are
# metres east (x) / north (y) of this point.
ORIGIN_LAT, ORIGIN_LON = 40.7000, -89.6000
_M_PER_DEG_LAT = 111_111.0
_M_PER_DEG_LON = 111_111.0 * math.cos(math.radians(ORIGIN_LAT))


def to_latlon(x, y):
    return ORIGIN_LAT + y / _M_PER_DEG_LAT, ORIGIN_LON + x / _M_PER_DEG_LON


def from_latlon(lat, lon):
    return (lon - ORIGIN_LON) * _M_PER_DEG_LON, (lat - ORIGIN_LAT) * _M_PER_DEG_LAT


# Places where people on foot and machines mix. Proximity events cluster here.
HOT_ZONES = [
    {"zone_id": "HZ1", "name": "Crew work area", "x": 120, "y": 380, "radius_m": 40},
    {"zone_id": "HZ2", "name": "Haul road crossing", "x": 300, "y": 250, "radius_m": 35},
    {"zone_id": "HZ3", "name": "Fuel & service bay", "x": 480, "y": 100, "radius_m": 30},
]

# The excavation edge: terrain gets steep here (slope events).
EXCAVATION_EDGE = {"x": 200, "y": 150, "sigma_m": 40, "peak_deg": 12}

MACHINES = [
    # machine_id, model, type, age, home_x, home_y, capacity_kg, size_factor
    {"machine_id": "M01", "model": "Cat 336 Excavator", "machine_type": "excavator", "machine_age_yrs": 3, "home_x": 215, "home_y": 215, "capacity_kg": 4200, "size": 1.3},
    {"machine_id": "M02", "model": "Cat 320 Excavator", "machine_type": "excavator", "machine_age_yrs": 7, "home_x": 150, "home_y": 330, "capacity_kg": 2600, "size": 1.0},
    {"machine_id": "M03", "model": "Cat 950 Wheel Loader", "machine_type": "wheel_loader", "machine_age_yrs": 5, "home_x": 330, "home_y": 300, "capacity_kg": 6000, "size": 1.2},
    {"machine_id": "M04", "model": "Cat 966 Wheel Loader", "machine_type": "wheel_loader", "machine_age_yrs": 9, "home_x": 420, "home_y": 160, "capacity_kg": 7200, "size": 1.4},
    {"machine_id": "M05", "model": "Cat D6 Dozer", "machine_type": "dozer", "machine_age_yrs": 2, "home_x": 270, "home_y": 80, "capacity_kg": 0, "size": 1.2},
    {"machine_id": "M06", "model": "Cat 745 Articulated Truck", "machine_type": "articulated_truck", "machine_age_yrs": 6, "home_x": 300, "home_y": 220, "capacity_kg": 41000, "size": 1.6},
]
MACHINE_BY_ID = {m["machine_id"]: m for m in MACHINES}

# Behavioural archetypes shape telemetry; comprehension archetypes shape how
# an operator reacts to ARC's messages (feedback loop 2).
OPERATORS = [
    {"operator_id": "OP01", "name": "Op. Alvarez", "tenure_months": 96, "behavioral_archetype": "smooth_experienced", "comprehension_profile": "fast_confident"},
    {"operator_id": "OP02", "name": "Op. Brooks", "tenure_months": 60, "behavioral_archetype": "smooth_experienced", "comprehension_profile": "cautious_rechecker"},
    {"operator_id": "OP03", "name": "Op. Chen", "tenure_months": 40, "behavioral_archetype": "aggressive_fast", "comprehension_profile": "fast_confident"},
    {"operator_id": "OP04", "name": "Op. Dube", "tenure_months": 30, "behavioral_archetype": "aggressive_fast", "comprehension_profile": "low_trust_dismisser"},
    {"operator_id": "OP05", "name": "Op. Eriksen", "tenure_months": 6, "behavioral_archetype": "cautious_beginner", "comprehension_profile": "cautious_rechecker"},
    {"operator_id": "OP06", "name": "Op. Farouk", "tenure_months": 9, "behavioral_archetype": "cautious_beginner", "comprehension_profile": "low_trust_dismisser"},
    {"operator_id": "OP07", "name": "Op. Garcia", "tenure_months": 120, "behavioral_archetype": "smooth_experienced", "comprehension_profile": "low_trust_dismisser"},
    {"operator_id": "OP08", "name": "Op. Haddad", "tenure_months": 18, "behavioral_archetype": "aggressive_fast", "comprehension_profile": "cautious_rechecker"},
]
OPERATOR_BY_ID = {o["operator_id"]: o for o in OPERATORS}

# Numeric personality of each behavioural archetype.
ARCHETYPE_PARAMS = {
    "smooth_experienced": dict(jerk=0.22, jerk_sd=0.04, input_rate=34, cycle_s=40, idle_bias=0.10, fatigue_rate=0.7, risk=0.8, belt_off=0.8),
    "aggressive_fast":    dict(jerk=0.42, jerk_sd=0.07, input_rate=50, cycle_s=33, idle_bias=0.07, fatigue_rate=1.0, risk=1.4, belt_off=2.0),
    "cautious_beginner":  dict(jerk=0.32, jerk_sd=0.09, input_rate=27, cycle_s=55, idle_bias=0.20, fatigue_rate=1.3, risk=1.2, belt_off=0.7),
}

TASK_TYPES = {
    "excavator": [("trenching", 55), ("truck_loading", 40), ("bulk_excavation", 70)],
    "wheel_loader": [("stockpile_loading", 45), ("truck_loading", 35), ("yard_cleanup", 30)],
    "dozer": [("grading", 60), ("ripping", 50), ("pushing", 40)],
    "articulated_truck": [("hauling", 45), ("haul_cycle_long", 65)],
}


def dist_to_zone(x, y, zone):
    return math.hypot(x - zone["x"], y - zone["y"]) - zone["radius_m"]


def nearest_hot_zone(x, y):
    """Return (zone, metres outside the zone boundary; negative = inside)."""
    best = min(HOT_ZONES, key=lambda z: dist_to_zone(x, y, z))
    return best, dist_to_zone(x, y, best)


def terrain_slope(x, y):
    """Smooth synthetic terrain: gentle rolling ground + a steep excavation edge."""
    e = EXCAVATION_EDGE
    d2 = (x - e["x"]) ** 2 + (y - e["y"]) ** 2
    edge = e["peak_deg"] * math.exp(-d2 / (2 * e["sigma_m"] ** 2))
    rolling = 2.5 + 1.5 * math.sin(x / 55.0) * math.cos(y / 70.0)
    return max(0.0, rolling + edge)
