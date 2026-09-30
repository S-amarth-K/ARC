"""
FastAPI backend.

    python -m backend.server          (http://localhost:8000, docs at /docs)

WebSockets
  /ws/ingest            telematics in (simulator / demo). Messages: session | tick | shift_state
  /ws/hud/{machine_id}  Operator HUD. Receives hud/event/feedback/calibration/demo; sends ack | why | shift_state
  /ws/supervisor        Supervisor dashboard live feed (every logged event, feedback, calibration change)

REST — see the route list below (or /docs).
"""
import asyncio
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, analytics
from .config import HARD_SAFETY_THRESHOLD, ROOT
from .db import connect, rows
from .messages import MESSAGES, audio_ref
from .ml.models import Intelligence
from .pipeline import Engine, _public


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)


def dump(o):
    return json.dumps(o, default=_default)


class Hub:
    """Fan-out of engine output to WebSocket subscribers, in order."""

    def __init__(self):
        self.hud = defaultdict(set)      # machine_id -> sockets ('*' = all machines)
        self.sup = set()
        self.queue: asyncio.Queue = asyncio.Queue()

    def emit(self, channel, payload):
        self.queue.put_nowait((channel, payload))

    async def pump(self):
        while True:
            channel, payload = await self.queue.get()
            msg = dump({**payload, "type": channel} if channel != "hud" else payload)
            mid = payload.get("machine_id")
            targets = set(self.hud.get(mid, set())) | set(self.hud.get("*", set()))
            if channel != "hud":
                targets |= self.sup
            for ws in list(targets):
                try:
                    await ws.send_text(msg)
                except Exception:
                    self._drop(ws)

    def _drop(self, ws):
        self.sup.discard(ws)
        for s in self.hud.values():
            s.discard(ws)


hub = Hub()
state = {}


@asynccontextmanager
async def lifespan(app):
    conn = connect()
    state["conn"] = conn
    state["engine"] = Engine(conn, Intelligence(conn), source="live", emit=hub.emit)
    state["demo_task"] = None
    pump = asyncio.create_task(hub.pump())
    yield
    pump.cancel()
    conn.close()


app = FastAPI(title="ARC — Attention & Risk Compression", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def E() -> Engine:
    return state["engine"]


def C():
    return state["conn"]


# ---------------------------------------------------------------------------
# Ingest (simulator / telematics)
# ---------------------------------------------------------------------------
def handle_ingest(msg: dict):
    kind = msg.get("type", "tick")
    if kind == "session":
        E().open_session(msg["machine_id"], msg["operator_id"], msg["shift_id"],
                         datetime.fromisoformat(msg["shift_start"]), source=msg.get("source"))
        if msg.get("shift_state"):
            E().set_shift_state(msg["machine_id"], msg["shift_state"])
        return {"ok": True, "session": msg["machine_id"]}
    if kind == "shift_state":
        E().set_shift_state(msg["machine_id"], msg["state"])
        return {"ok": True}
    if kind == "tick":
        E().on_tick(msg)
        return None
    return {"error": f"unknown message type {kind}"}


@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = json.loads(await ws.receive_text())
            for msg in data if isinstance(data, list) else [data]:
                try:
                    reply = handle_ingest(msg)
                except KeyError as e:
                    reply = {"error": str(e)}
                if reply:
                    await ws.send_text(dump(reply))
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Operator HUD
# ---------------------------------------------------------------------------
@app.websocket("/ws/hud/{machine_id}")
async def ws_hud(ws: WebSocket, machine_id: str):
    await ws.accept()
    hub.hud[machine_id].add(ws)
    s = E().sessions.get(machine_id)
    if s and s.last_hud:
        await ws.send_text(dump(s.last_hud))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if machine_id not in E().sessions:
                continue
            if kind == "ack":
                E().ack(machine_id, msg["event_id"], msg.get("response", "acknowledged"))
            elif kind == "why":
                E().why_opened(machine_id, msg["event_id"])
            elif kind == "shift_state":
                E().set_shift_state(machine_id, msg["state"])
    except WebSocketDisconnect:
        hub._drop(ws)


@app.websocket("/ws/supervisor")
async def ws_supervisor(ws: WebSocket):
    await ws.accept()
    hub.sup.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub._drop(ws)


@app.get("/api/sessions")
def sessions():
    out = []
    for mid, s in E().sessions.items():
        out.append({"machine_id": mid, "operator_id": s.operator_id, "shift_id": s.shift_id, "shift_state": s.shift_state,
                    "source": s.source, "risk": round(s.last_risk, 3), "last_ts": (s.last_feat or {}).get("timestamp")})
    return out


@app.get("/api/hud/{machine_id}")
def hud(machine_id: str):
    s = E().sessions.get(machine_id)
    return s.last_hud if s else None


class Ack(BaseModel):
    event_id: int
    response: str = "acknowledged"


@app.post("/api/hud/{machine_id}/ack")
def ack(machine_id: str, body: Ack):
    return E().ack(machine_id, body.event_id, body.response)


class ShiftState(BaseModel):
    state: str


@app.post("/api/hud/{machine_id}/shift_state")
def shift_state(machine_id: str, body: ShiftState):
    if machine_id not in E().sessions:
        raise HTTPException(404, "no session for machine")
    return E().set_shift_state(machine_id, body.state)


@app.get("/api/tasks/{machine_id}")
def tasks(machine_id: str):
    """Task queue + foreman trade-off. Only served when the machine isn't in active operation."""
    s = E().sessions.get(machine_id)
    if not s:
        raise HTTPException(404, "no session for machine")
    if s.shift_state == "active":
        raise HTTPException(423, "Task screen is locked while the machine is operating (pre-shift / break / post-shift only).")
    return analytics.tradeoff(E(), machine_id)


class Decision(BaseModel):
    option_id: str
    decided_by: str = "foreman"


@app.post("/api/tasks/{machine_id}/decide")
def decide(machine_id: str, body: Decision):
    s = E().sessions.get(machine_id)
    t = analytics.tradeoff(E(), machine_id)
    opt = next((o for o in t["options"] if o["id"] == body.option_id), None)
    if not opt:
        raise HTTPException(400, "unknown option")
    ts = (s.last_feat or {}).get("timestamp") or datetime.now().isoformat(timespec="seconds")
    reason = f"{body.decided_by} chose '{opt['label']}' (overrun {opt['overrun_min']} min, risk delta {opt['risk_delta']:+.2f})"
    cur = C().execute("""INSERT INTO event_log(timestamp, machine_id, operator_id, tier, message_text, trigger_reason,
                        risk_score_at_trigger, shift_id, message_key, surfaced_to_operator, top_features, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (ts, machine_id, s.operator_id, 2, MESSAGES["T2_FOREMAN"]["text"], reason, s.last_risk, s.shift_id,
                      "T2_FOREMAN", 0, "[]", s.source))
    C().commit()
    ev = {"event_id": cur.lastrowid, "timestamp": ts, "machine_id": machine_id, "operator_id": s.operator_id, "tier": 2,
          "message_key": "T2_FOREMAN", "message_text": MESSAGES["T2_FOREMAN"]["text"], "trigger_reason": reason,
          "surfaced_to_operator": 0, "source": s.source}
    hub.emit("event", ev)
    return {"ok": True, "event": ev}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
def _event(event_id):
    r = rows(C(), "SELECT * FROM event_log WHERE event_id=?", (event_id,))
    if not r:
        raise HTTPException(404, "event not found")
    ev = r[0]
    from .ml.features import FEATURE_LABELS
    tops = json.loads(ev["top_features"] or "[]")
    ev["why_labels"] = [FEATURE_LABELS.get(f, f) for f, _ in tops]
    if ev["message_key"] == "T4_TASK_DELAY":   # driven by the task-time model, not risk contributors
        ev["why_labels"] = ["task predicted to run past its plan"]
    ref = ev.get("signal_snapshot_ref") or ""
    if ref.startswith("snapshot:"):
        snap = C().execute("SELECT payload FROM signal_snapshot WHERE snapshot_id=?", (int(ref.split(":")[1]),)).fetchone()
        ev["snapshot"] = json.loads(snap[0]) if snap else {}
    ev["feedback"] = rows(C(), "SELECT * FROM feedback_log WHERE event_id=?", (event_id,))
    return ev


@app.get("/api/events/{event_id}")
def event(event_id: int):
    return _event(event_id)


@app.post("/api/why/{event_id}")
def why(event_id: int):
    """Agent 1: bounded in-cab explanation (single turn, 1-2 sentences). Also records Loop-2 'why opened'."""
    ev = _event(event_id)
    if ev["tier"] < 4:
        raise HTTPException(400, "'why' is only available for tier-4/5 events")
    E().why_opened(ev["machine_id"], event_id) if ev["machine_id"] in E().sessions else None
    return {"event_id": event_id, **agents.explain_event(ev)}


class Chat(BaseModel):
    machine_id: str
    messages: list[dict]


@app.post("/api/briefing")
def briefing(body: Chat):
    """Agent 2: conversational briefing — refused while the machine is operating."""
    s = E().sessions.get(body.machine_id)
    if not s:
        raise HTTPException(404, "no session for machine")
    if s.shift_state == "active":
        raise HTTPException(423, "Briefing is only available pre-shift, on break, or post-shift.")
    return agents.briefing_reply(C(), E(), body.machine_id, s.operator_id, body.messages)


class Note(BaseModel):
    machine_id: str
    note: str


@app.post("/api/briefing/note")
def add_note(body: Note):
    s = E().sessions.get(body.machine_id)
    C().execute("INSERT INTO shift_note(shift_id, machine_id, operator_id, timestamp, note) VALUES (?,?,?,?,?)",
                (s.shift_id if s else None, body.machine_id, s.operator_id if s else None, datetime.now().isoformat(timespec="seconds"), body.note))
    C().commit()
    return {"ok": True}


class Query(BaseModel):
    question: str


@app.post("/api/supervisor/query")
def supervisor_query(body: Query):
    """Agent 3: natural-language questions over event_log / feedback_log (read-only SQL tool)."""
    return agents.supervisor_query(C(), body.question)


# ---------------------------------------------------------------------------
# Supervisor analytics
# ---------------------------------------------------------------------------
@app.get("/api/meta")
def meta():
    ms = rows(C(), "SELECT * FROM machine")
    ops = rows(C(), "SELECT * FROM operator_profile")
    return {"machines": ms, "operators": ops, "hard_threshold": HARD_SAFETY_THRESHOLD,
            "llm": agents.llm_available(), "llm_model": agents.GEMINI_MODEL, "models": E().intel.meta.get("risk_report"), "task_model": E().intel.meta.get("task_report"),
            "messages": {k: {**v, "audio": audio_ref(k)} for k, v in MESSAGES.items()}}


@app.get("/api/events")
def events(tier: int | None = None, min_tier: int | None = None, operator_id: str | None = None, machine_id: str | None = None,
           source: str | None = None, shift_id: str | None = None, limit: int = 200):
    q, p = ["1=1"], []
    for col, val in (("tier", tier), ("operator_id", operator_id), ("machine_id", machine_id), ("source", source), ("shift_id", shift_id)):
        if val is not None:
            q.append(f"e.{col}=?")
            p.append(val)
    if min_tier is not None:
        q.append("e.tier>=?")
        p.append(min_tier)
    return rows(C(), f"""SELECT e.*, f.effectiveness_label, f.ack_response, f.why_drawer_opened, f.reaction_latency_s
                          FROM event_log e LEFT JOIN feedback_log f USING(event_id) WHERE {' AND '.join(q)}
                          ORDER BY e.timestamp DESC, e.event_id DESC LIMIT ?""", (*p, min(limit, 2000)))


@app.get("/api/analytics/summary")
def a_summary():
    return analytics.summary(C())


@app.get("/api/analytics/trends")
def a_trends():
    return analytics.trends(C())


@app.get("/api/analytics/operators")
def a_operators():
    return analytics.operator_summary(C())


@app.get("/api/calibration/history")
def cal_history(operator_id: str | None = None):
    return analytics.calibration_history(C(), operator_id)


@app.get("/api/checklist")
def checklist(mismatch_only: bool = True):
    return analytics.checklist(C(), mismatch_only)


@app.get("/api/training/{operator_id}")
def training(operator_id: str):
    return analytics.training_recommendations(C(), operator_id, top_n=5)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
@app.post("/api/demo/start")
async def demo_start():
    from .demo import run_demo
    t = state.get("demo_task")
    if t and not t.done():
        return {"ok": False, "status": "demo already running"}
    state["demo_task"] = asyncio.create_task(run_demo(E(), hub.emit))
    return {"ok": True, "status": "demo started", "machine_id": "M02"}


# Serve the built frontend if present (npm run build) so one process can host everything.
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")
    if (DIST / "audio").exists():
        app.mount("/audio", StaticFiles(directory=DIST / "audio"), name="audio")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")


def main():
    import uvicorn
    uvicorn.run("backend.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
