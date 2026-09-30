"""
SQLite schema + tiny helpers.

The six tables from the brief (machine_telemetry, operator_profile,
task_queue, event_log, feedback_log, checklist_log) are defined exactly as
specified; a few columns and support tables are ADDED (never renamed) and are
marked `-- ARC extra` so it's obvious what goes beyond the brief.
"""
import json
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS machine (                 -- ARC extra
    machine_id TEXT PRIMARY KEY,
    model TEXT, machine_type TEXT, machine_age_yrs REAL,
    home_x REAL, home_y REAL
);

CREATE TABLE IF NOT EXISTS hot_zone (                -- ARC extra
    zone_id TEXT PRIMARY KEY, name TEXT,
    x REAL, y REAL, radius_m REAL, lat REAL, lon REAL
);

CREATE TABLE IF NOT EXISTS shift (                   -- ARC extra
    shift_id TEXT PRIMARY KEY, date TEXT, start_ts TEXT, end_ts TEXT,
    status TEXT                                      -- completed | scheduled
);

CREATE TABLE IF NOT EXISTS operator_profile (
    operator_id TEXT PRIMARY KEY,
    name TEXT,                                       -- anonymised display name
    tenure_months INTEGER,
    skill_level TEXT,
    certification_flags TEXT,                        -- comma separated
    comprehension_profile TEXT,                      -- fast_confident | cautious_rechecker | low_trust_dismisser
    behavioral_archetype TEXT                        -- smooth_experienced | aggressive_fast | cautious_beginner
);

CREATE TABLE IF NOT EXISTS machine_telemetry (
    timestamp TEXT, machine_id TEXT, operator_id TEXT,
    engine_hours REAL, fuel_used_L REAL, fuel_level_pct REAL,
    engine_load_pct REAL, rpm REAL,
    hydraulic_pressure_psi REAL, hydraulic_temp_C REAL, coolant_temp_C REAL,
    load_cycles INTEGER, payload_kg REAL, idling_time_min REAL,
    joystick_input_rate REAL, joystick_jerk REAL,
    proximity_distance_m REAL, proximity_direction TEXT,
    seatbelt_status INTEGER,
    gps_lat REAL, gps_lon REAL, terrain_slope_deg REAL,
    ambient_temp_C REAL, visibility REAL, weather_condition TEXT,
    shift_id TEXT,                                   -- ARC extra
    activity_state TEXT,                             -- ARC extra: working|idling|traveling|off
    ground_speed_kmh REAL,                           -- ARC extra
    weather_severity REAL,                           -- ARC extra 0..1
    unsafe_event_type TEXT                           -- ARC extra: synthetic ground truth label
);
CREATE INDEX IF NOT EXISTS ix_tel_ms ON machine_telemetry(machine_id, shift_id, timestamp);

CREATE TABLE IF NOT EXISTS task_queue (
    task_id TEXT PRIMARY KEY, machine_id TEXT, operator_id TEXT,
    task_type TEXT, assigned_start TEXT,
    estimated_time_min REAL, actual_time_min REAL,
    weather TEXT, machine_age_yrs REAL, status TEXT,
    shift_id TEXT, seq INTEGER                        -- ARC extra
);

CREATE TABLE IF NOT EXISTS checklist_log (
    shift_id TEXT, operator_id TEXT, field TEXT,
    operator_entered_value TEXT, sensor_ground_truth_value TEXT,
    mismatch_flag INTEGER,
    machine_id TEXT                                  -- ARC extra
);

CREATE TABLE IF NOT EXISTS signal_snapshot (         -- ARC extra: what the engine saw
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT                                     -- JSON of engineered features
);

CREATE TABLE IF NOT EXISTS event_log (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, machine_id TEXT, operator_id TEXT,
    signal_snapshot_ref TEXT,
    tier INTEGER,
    message_text TEXT, audio_ref TEXT,
    trigger_reason TEXT,
    risk_score_at_trigger REAL,
    shift_id TEXT,                                   -- ARC extra
    message_key TEXT,                                -- ARC extra: key into fixed message library
    surfaced_to_operator INTEGER,                    -- ARC extra: 0 = logged only
    top_features TEXT,                               -- ARC extra: JSON [[feature, contribution], ...]
    source TEXT                                      -- ARC extra: historical | live | demo
);
CREATE INDEX IF NOT EXISTS ix_event_op ON event_log(operator_id, timestamp);

CREATE TABLE IF NOT EXISTS feedback_log (
    event_id INTEGER REFERENCES event_log(event_id),
    reaction_latency_s REAL,                         -- Loop 1: time to corrective change in telemetry
    corrective_action_taken INTEGER,                 -- Loop 1
    metric_returned_to_baseline INTEGER,             -- Loop 1
    why_drawer_opened INTEGER,                       -- Loop 2
    ack_response TEXT,                               -- Loop 2: acknowledged | dismissed | no_response
    effectiveness_label TEXT,                        -- effective | partial | ineffective | ignored
    ack_latency_s REAL,                              -- ARC extra (Loop 2)
    source TEXT                                      -- ARC extra
);

CREATE TABLE IF NOT EXISTS operator_baseline (       -- ARC extra: personalised baseline
    operator_id TEXT, metric TEXT, mean REAL, std REAL,
    PRIMARY KEY (operator_id, metric)
);

CREATE TABLE IF NOT EXISTS operator_calibration (    -- ARC extra: current policy per operator
    operator_id TEXT PRIMARY KEY,
    soft_threshold REAL, ambient_z REAL,
    channel TEXT,                                    -- visual | visual_audio
    trust_score REAL, updated_at TEXT, last_reason TEXT
);

CREATE TABLE IF NOT EXISTS calibration_history (     -- ARC extra: audit trail of every policy change
    operator_id TEXT, timestamp TEXT,
    soft_threshold REAL, ambient_z REAL, channel TEXT,
    trust_score REAL, reason TEXT
);

CREATE TABLE IF NOT EXISTS shift_note (              -- ARC extra: handoff notes from briefing agent
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id TEXT, machine_id TEXT, operator_id TEXT,
    timestamp TEXT, note TEXT
);
"""


def connect(path=None) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def session(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(conn, sql, params=()):
    """Run a query and return a list of plain dicts (JSON-serialisable)."""
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def dumps(obj) -> str:
    return json.dumps(obj, default=float)
