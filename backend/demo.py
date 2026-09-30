"""
Phase 8 — Demo mode: a pre-scripted shift segment on M02 (Op. Dube, OP04).

    python -m backend.demo            # triggers the demo on a running backend
    (or press "Run demo" in the HUD's judge panel)

Storyline (≈90 s):
  1. QUIET      Late in the shift, normal digging. Ambient strip green. ARC
                silently switches to the rear camera once (tier 1, logged).
  2. BUILD-UP   Rain rolls in, the machine repositions toward the crew area,
                controls get sharper, and an object closes in from the rear.
                The judge panel shows the risk score climbing past OP04's
                soft threshold (a tier-4 card with audio: OP04 has been
                calibrated to "fewer but louder" because they ignore nudges).
  3. TIER 5     Person inside 3 m while moving -> hard rule -> "STOP." with
                audio; it cannot be dismissed, only acknowledged.
  4. RECOVERY   Machine stops, the person moves off. Loop 1 (did it work?)
                and Loop 2 (did they understand?) are scored and logged, and
                the calibration policy is re-evaluated.
  5. BREAK      Shift state -> break: the task screen unlocks and shows the
                foreman trade-off (task-time model + risk model).
  6. SUPERVISOR Hand over to the supervisor dashboard.

Every tick goes through exactly the same Engine.on_tick() as live telemetry.
"""
import asyncio
import math
import random
from datetime import datetime, timedelta

from .config import LIVE_SHIFT_ID, TICK_SECONDS
from .db import rows
from .site_model import MACHINE_BY_ID, terrain_slope, to_latlon

DEMO_MACHINE, DEMO_OPERATOR = "M02", "OP04"
TICK_REAL_S = 1.5             # real seconds between scripted ticks
START_ELAPSED_H = 5.55        # scenario starts 5h33m into the shift (fatigue is real by now)
CRASH_SITE = (126.0, 371.0)   # just inside the crew work area (HZ1)

STAGES = {
    "quiet": "Quiet: normal digging late in the shift. Ambient strip green.",
    "build": "Risk builds: rain, repositioning toward the crew area, sharper controls, object closing from the rear.",
    "tier5": "Tier 5: STOP. The hard floor is never calibrated away. Waiting for the operator to acknowledge…",
    "recovery": "Recovery: machine stopped, person clear. Scoring Loop 1 + Loop 2.",
    "break": "Break: task screen unlocked — foreman trade-off.",
    "supervisor": "Hand-over: supervisor dashboard.",
    "done": "Demo complete.",
}


def demo_tasks():
    """M02's plan for today, arranged so the current task is mid-way and the plan won't fit the shift."""
    plan = [("trenching", 55, 10), ("truck_loading", 40, 75), ("trenching", 55, 125), ("truck_loading", 40, 190),
            ("bulk_excavation", 70, 240), ("bulk_excavation", 70, 322), ("trenching", 55, 400), ("truck_loading", 40, 460)]
    return [{"task_id": f"D{i + 1:02d}", "machine_id": DEMO_MACHINE, "operator_id": DEMO_OPERATOR, "task_type": tt,
             "estimated_time_min": float(est), "machine_age_yrs": MACHINE_BY_ID[DEMO_MACHINE]["machine_age_yrs"],
             "weather": "rain" if off >= 322 else "clear", "start_offset_min": float(off), "status": "pending", "seq": i}
            for i, (tt, est, off) in enumerate(plan)]


class Script:
    """Generates scripted telemetry ticks with continuous counters (fuel, cycles, hours...)."""

    def __init__(self, t0: datetime):
        self.t = t0
        self.rng = random.Random(7)
        self.x, self.y = MACHINE_BY_ID[DEMO_MACHINE]["home_x"], MACHINE_BY_ID[DEMO_MACHINE]["home_y"]
        self.cycles = 540.0
        self.fuel_used, self.fuel = 118.0, 61.0
        self.idle_min = 38.0
        self.engine_hours = 11562.0
        self.hyd = 71.0

    def tick(self, activity="working", jerk=0.49, input_rate=50, prox=50.0, pdir="none", belt=1, speed=None,
             weather="clear", wsev=0.0, visibility=10000, toward=None, step_m=0.0):
        r = self.rng
        if toward and step_m:
            dx, dy = toward[0] - self.x, toward[1] - self.y
            d = math.hypot(dx, dy)
            if d > 0.5:
                self.x += dx / d * min(step_m, d)
                self.y += dy / d * min(step_m, d)
        work = activity == "working"
        load = (72 + r.gauss(0, 4)) if work else (46 if activity == "traveling" else 18) + r.gauss(0, 2)
        if work:
            self.cycles += TICK_SECONDS / (33 * (1.08 + 0.1 * wsev))
        if activity == "idling":
            self.idle_min += 0.5
        rate = 3.5 if activity == "idling" else 6 + 0.28 * load
        self.fuel_used += rate * TICK_SECONDS / 3600
        self.fuel -= rate * TICK_SECONDS / 3600 / 380 * 100
        self.engine_hours += TICK_SECONDS / 3600
        self.hyd += ((42 + load * 0.38 + 7.7) - self.hyd) * 0.04
        lat, lon = to_latlon(self.x, self.y)
        tick = dict(
            type="tick", timestamp=self.t.isoformat(timespec="seconds"), machine_id=DEMO_MACHINE, operator_id=DEMO_OPERATOR,
            engine_hours=round(self.engine_hours, 2), fuel_used_L=round(self.fuel_used, 2), fuel_level_pct=round(self.fuel, 2),
            engine_load_pct=round(load, 1), rpm=round(800 if activity == "idling" else 1100 + load * 9),
            hydraulic_pressure_psi=round(2400 + load * 24 if work else 1200 if activity == "traveling" else 550),
            hydraulic_temp_C=round(self.hyd, 1), coolant_temp_C=round(86 + load * 0.05, 1),
            load_cycles=int(self.cycles), payload_kg=round(2600 * 0.9) if work else 0, idling_time_min=self.idle_min,
            joystick_input_rate=round(abs(r.gauss(input_rate, 3)) if activity != "idling" else 3, 1),
            joystick_jerk=round(abs(r.gauss(jerk, 0.03)) * (0.2 if activity == "idling" else 1), 3),
            proximity_distance_m=round(prox, 1), proximity_direction=pdir, seatbelt_status=belt,
            gps_lat=round(lat, 6), gps_lon=round(lon, 6), terrain_slope_deg=round(terrain_slope(self.x, self.y), 1),
            ambient_temp_C=17.5, visibility=visibility, weather_condition=weather, shift_id=LIVE_SHIFT_ID,
            activity_state=activity, ground_speed_kmh=speed if speed is not None else (0.3 if work else 0.0),
            weather_severity=wsev,
        )
        self.t += timedelta(seconds=TICK_SECONDS)
        return tick


def steps():
    """The scripted demo as a list of steps: (kind, payload, real_delay_s)."""
    now = datetime.now().replace(microsecond=0)
    n_prefill = 30
    t0 = now - timedelta(seconds=TICK_SECONDS * n_prefill)
    shift_start = t0 - timedelta(hours=START_ELAPSED_H)
    sc = Script(t0)
    out = [("session", dict(type="session", machine_id=DEMO_MACHINE, operator_id=DEMO_OPERATOR, shift_id=LIVE_SHIFT_ID,
                            shift_start=shift_start.isoformat(timespec="seconds"), source="demo"), 0)]
    # Pre-fill 15 min of normal history instantly so rolling windows are warm.
    for _ in range(n_prefill):
        out.append(("tick", sc.tick(), 0))

    out.append(("stage", "quiet", 0))
    for i in range(10):
        rear = i == 5
        out.append(("tick", sc.tick(prox=12.0 if rear else 50.0, pdir="rear" if rear else "none"), TICK_REAL_S))

    out.append(("stage", "build", 0))
    n = 16
    for i in range(n):
        p = i / (n - 1)
        weather = dict(weather="rain", wsev=round(0.35 + 0.5 * p, 2), visibility=round(4200 - 2600 * p))
        prox = 50.0 if i < 3 else max(2.4, 24 - (24 - 2.4) * (i - 3) / (n - 4))
        moving = "traveling" if i < 5 else "working"
        out.append(("tick", sc.tick(activity=moving, jerk=0.50 + 0.36 * p, input_rate=52 + 22 * p,
                                    prox=prox, pdir="rear" if prox < 50 else "none",
                                    speed=6.0 if moving == "traveling" else 1.8, toward=CRASH_SITE,
                                    step_m=12 if moving == "traveling" else 1.5, **weather), TICK_REAL_S))
    # (the runner jumps here as soon as a tier-5 intervention has been acknowledged)
    out.append(("stage", "recovery", 0))
    for i, prox in enumerate([3.5, 6.0, 10.0, 16.0, 30.0, 50, 50, 50, 50, 50, 50, 50, 50]):
        out.append(("tick", sc.tick(activity="idling", jerk=0.1, prox=prox, pdir="rear" if prox < 50 else "none",
                                    speed=0.0, weather="rain", wsev=0.6, visibility=2500), TICK_REAL_S * 0.8))

    out.append(("stage", "break", 0))
    out.append(("shift_state", dict(machine_id=DEMO_MACHINE, state="break"), 12))
    out.append(("stage", "supervisor", 6))
    out.append(("stage", "done", 0))
    return out


def seed_demo_plan(conn):
    """M01 (the other excavator) gets a lighter plan today so the foreman has somewhere to move work."""
    conn.execute("DELETE FROM task_queue WHERE shift_id=? AND machine_id='M01' AND seq >= 5", (LIVE_SHIFT_ID,))
    conn.commit()


def seed_demo_checklist(conn):
    """Today's pre-shift checklist for M02 with one entry that disagrees with the sensor (-> tier 2, supervisor only)."""
    conn.execute("DELETE FROM checklist_log WHERE shift_id=? AND machine_id=?", (LIVE_SHIFT_ID, DEMO_MACHINE))
    for field, entered, truth in [("fuel_level_pct", "80", "55"), ("engine_hours", "11556", "11556"),
                                  ("seatbelt_functional", "ok", "ok"), ("backup_alarm_ok", "ok", "ok"),
                                  ("hydraulic_leak_check", "ok", "ok")]:
        conn.execute("INSERT INTO checklist_log VALUES (?,?,?,?,?,?,?)",
                     (LIVE_SHIFT_ID, DEMO_OPERATOR, field, entered, truth, int(entered != truth), DEMO_MACHINE))
    conn.commit()


async def run_demo(engine, emit, ack_wait_poll=0.25, ack_timeout_s=30):
    """
    Server-side runner: plays steps() through the same engine used for live telemetry.
    It is reactive: when the engine raises a tier-5 intervention, the runner
    announces it, waits (up to ack_timeout_s) for a human to press ACKNOWLEDGE
    on the HUD, then skips straight to the recovery segment.
    """
    seed_demo_checklist(engine.conn)
    seed_demo_plan(engine.conn)
    skip_to_recovery = False
    clock = None      # re-stamp ticks so skipped ticks don't leave a gap in simulated time
    for kind, payload, delay in steps():
        if skip_to_recovery and not (kind == "stage" and payload == "recovery"):
            continue
        skip_to_recovery = False
        if kind == "session":
            engine.open_session(payload["machine_id"], payload["operator_id"], payload["shift_id"],
                                datetime.fromisoformat(payload["shift_start"]), tasks=demo_tasks(), source="demo")
            emit("demo", {"stage": "start", "machine_id": DEMO_MACHINE, "text": "Demo starting on M02 (Op. Dube)"})
        elif kind == "tick":
            clock = datetime.fromisoformat(payload["timestamp"]) if clock is None else clock + timedelta(seconds=TICK_SECONDS)
            payload["timestamp"] = clock.isoformat(timespec="seconds")
            engine.on_tick(payload)
            s = engine.sessions[DEMO_MACHINE]
            iv = s.gate.active_intervention
            if iv and payload.get("activity_state") != "idling":
                emit("demo", {"stage": "tier5", "machine_id": DEMO_MACHINE,
                              "text": f"{STAGES['tier5']} ({iv['trigger_reason']})"})
                waited = 0.0
                while s.gate.active_intervention and waited < ack_timeout_s:
                    await asyncio.sleep(ack_wait_poll)
                    waited += ack_wait_poll
                skip_to_recovery = True
                continue
        elif kind == "stage":
            emit("demo", {"stage": payload, "machine_id": DEMO_MACHINE, "text": STAGES[payload]})
        elif kind == "shift_state":
            engine.set_shift_state(payload["machine_id"], payload["state"])
        if delay:
            await asyncio.sleep(delay)


def main():
    """CLI: trigger the demo on the running backend and narrate the stages."""
    import json
    import sys
    import urllib.request

    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

    def start():
        req = urllib.request.Request(base + "/api/demo/start", method="POST")
        print(json.loads(urllib.request.urlopen(req).read()))

    try:
        import websockets

        async def follow():
            # Subscribe first so no stage narration is missed, then start the demo.
            async with websockets.connect(base.replace("http", "ws") + "/ws/supervisor") as ws:
                start()
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "demo":
                        print(f"[demo] {m['text']}")
                        if m.get("stage") == "done":
                            return
                    elif m.get("type") == "event" and m.get("tier", 0) >= 4:
                        print(f"   tier {m['tier']}: {m['message_text']}  (surfaced={bool(m['surfaced_to_operator'])})")
                    elif m.get("type") in ("feedback", "calibration"):
                        print(f"   {m['type']}: {json.dumps({k: v for k, v in m.items() if k != 'type'})}")
        asyncio.run(follow())
    except OSError as e:
        print("Backend not reachable — start it with `python -m backend.server`.", e)


if __name__ == "__main__":
    main()
