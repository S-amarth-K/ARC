"""
Telematics stand-in: replays a historical shift tick-by-tick over the
WebSocket (/ws/ingest), exactly as a real machine gateway would stream it.

    python -m backend.simulator                       # replay S12 as "today", all machines except M02, 2 ticks/s
    python -m backend.simulator --shift S07 --speed 5 --machines M01 M03 --from-hour 5

Timestamps are re-based onto today's shift (S13) so the replay reads as live;
each machine is re-assigned to its S13 rostered operator. M02 is left free for
the scripted demo unless you pass it explicitly.
"""
import argparse
import asyncio
import json
from datetime import datetime

import pandas as pd
import websockets

from .config import LIVE_SHIFT_ID, TICK_SECONDS
from .db import connect


async def replay(args):
    conn = connect()
    live = conn.execute("SELECT * FROM shift WHERE shift_id=?", (LIVE_SHIFT_ID,)).fetchone()
    src = conn.execute("SELECT * FROM shift WHERE shift_id=?", (args.shift,)).fetchone()
    roster = dict(conn.execute("SELECT machine_id, operator_id FROM task_queue WHERE shift_id=? GROUP BY machine_id", (LIVE_SHIFT_ID,)).fetchall())
    ph = ",".join("?" * len(args.machines))
    tel = pd.read_sql(f"SELECT * FROM machine_telemetry WHERE shift_id=? AND machine_id IN ({ph}) ORDER BY timestamp, machine_id",
                      conn, params=(args.shift, *args.machines))
    src_start, live_start = datetime.fromisoformat(src["start_ts"]), datetime.fromisoformat(live["start_ts"])
    tel["ts"] = pd.to_datetime(tel["timestamp"])
    tel = tel[tel["ts"] >= src_start + pd.Timedelta(hours=args.from_hour)]
    offset = live_start - src_start
    print(f"Replaying {args.shift} ({len(tel):,} ticks, {len(args.machines)} machines) as {LIVE_SHIFT_ID} at {args.speed} ticks/s per machine")

    async with websockets.connect(args.url, max_size=None) as ws:
        for m in args.machines:
            await ws.send(json.dumps({"type": "session", "machine_id": m, "operator_id": roster.get(m), "shift_id": LIVE_SHIFT_ID,
                                      "shift_start": live_start.isoformat(), "source": "live"}))
            print("  ", await ws.recv())
        for ts, group in tel.groupby("ts", sort=True):
            batch = []
            for rec in group.drop(columns=["ts", "unsafe_event_type"]).to_dict("records"):
                rec.update(type="tick", shift_id=LIVE_SHIFT_ID, operator_id=roster.get(rec["machine_id"]),
                           timestamp=(datetime.fromisoformat(rec["timestamp"]) + offset).isoformat())
                batch.append(rec)
            await ws.send(json.dumps(batch, default=str))
            await asyncio.sleep(1 / args.speed)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--shift", default="S12")
    p.add_argument("--speed", type=float, default=2.0, help="ticks per real second (1 tick = %ds simulated)" % TICK_SECONDS)
    p.add_argument("--machines", nargs="+", default=["M01", "M03", "M04", "M05", "M06"])
    p.add_argument("--from-hour", type=float, default=0.0, help="start this many hours into the shift")
    p.add_argument("--url", default="ws://localhost:8000/ws/ingest")
    asyncio.run(replay(p.parse_args()))


if __name__ == "__main__":
    main()
