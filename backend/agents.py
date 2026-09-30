"""
Phase 6 — The three LLM agents (Google Gemini). Deliberately NOT one chatbot.

1. explain_event()   In-cab, single turn, triggered only by "why" on a tier-4/5
                     event. Sees only that event's snapshot + contributing
                     features. Output hard-capped at 2 short sentences.
2. briefing_reply()  Pre/post-shift or break only (server refuses while the
                     shift is active). Conversational; grounded in the
                     operator's tasks, trade-off, flags, training gaps.
3. supervisor_query() Natural-language questions over event_log/feedback_log.
                     Uses a read-only SQL tool so every number in the answer
                     came from the database, not from the model's imagination.

Every agent has a deterministic, data-grounded fallback so the demo works with
no API key or no network.
"""
import json
import re
import sqlite3

from .config import DB_PATH, GEMINI_API_KEY, GEMINI_MODEL
from .db import rows

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = types = None

_client = None


def client():
    global _client
    if _client is None and genai is not None and GEMINI_API_KEY:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def llm_available():
    return client() is not None


def _contents(messages):
    """Chat history [{role: user|assistant, content}] -> Gemini Contents (roles: user | model)."""
    return [types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part.from_text(text=m["content"])])
            for m in messages]


def _config(system, max_tokens, quick=False, tools=None):
    cfg = dict(system_instruction=system, max_output_tokens=max_tokens, temperature=0.3)
    if quick and "flash" in GEMINI_MODEL:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)   # in-cab answer: no thinking, lowest latency
    if tools:
        cfg["tools"] = tools
        # We run the tool loop ourselves (bounded, and every SQL query is recorded for the UI).
        cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
    return types.GenerateContentConfig(**cfg)


def _call(system, contents, max_tokens=4096, quick=False, tools=None):
    """One Gemini generate_content call."""
    return client().models.generate_content(model=GEMINI_MODEL, contents=contents, config=_config(system, max_tokens, quick, tools))


def _text(resp):
    """Response text, or None if the response was blocked or empty."""
    try:
        return (resp.text or "").strip() or None
    except Exception:
        return None


def _cap_sentences(text, n=2, max_words=40):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = " ".join(parts[:n])
    words = out.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


# ---------------------------------------------------------------------------
# 1. In-cab bounded explanation
# ---------------------------------------------------------------------------
EXPLAIN_SYSTEM = """You explain one safety alert to a heavy-equipment operator who is in the cab right now.
Rules:
- Use ONLY the facts in the event data. Never invent readings, people, or causes.
- At most 2 short sentences, under 30 words total. Plain words a tired operator understands at a glance.
- Sentence 1: what the machine noticed (the top 1-2 reasons). Sentence 2 (optional): the one thing to do, matching the alert text.
- No numbers with more than 2 significant digits. No jargon (no "z-score", "SHAP", "probability model")."""


def explain_event(event: dict) -> dict:
    snap = event.get("snapshot") or {}
    labels = event.get("why_labels") or []
    facts = {
        "alert_text": event.get("message_text"), "tier": event.get("tier"),
        "trigger": event.get("trigger_reason"), "risk_score": event.get("risk_score_at_trigger"),
        "top_reasons_ranked": labels,
        "readings": {k: snap.get(k) for k in ("proximity_distance_m", "proximity_direction", "speed_kmh", "belt_off",
                                                "slope_deg", "weather_condition", "visibility", "shift_elapsed_h",
                                                "hyd_temp_C", "jerk_z") if snap.get(k) is not None},
    }
    fallback = _explain_template(facts)
    if not llm_available():
        return {"text": fallback, "source": "template"}
    try:
        resp = _call(EXPLAIN_SYSTEM, _contents([{"role": "user", "content": "Event data:\n" + json.dumps(facts, default=str)}]),
                     max_tokens=512, quick=True)
        txt = _text(resp)
        return {"text": _cap_sentences(txt) if txt else fallback, "source": "gemini" if txt else "template"}
    except Exception as e:  # never let an API problem reach the cab
        return {"text": fallback, "source": "template", "error": str(e)[:200]}


def _explain_template(facts):
    reasons = facts["top_reasons_ranked"][:2] or ["several signals rising together"]
    r = facts["readings"]
    detail = ""
    if r.get("proximity_distance_m") is not None and r["proximity_distance_m"] < 20:
        detail = f" Object {r['proximity_distance_m']:.0f} m {r.get('proximity_direction', '')}."
    return f"Flagged for {' and '.join(reasons)}.{detail}".replace("  ", " ")


# ---------------------------------------------------------------------------
# 2. Pre/post-shift briefing agent
# ---------------------------------------------------------------------------
BRIEFING_SYSTEM = """You are ARC's shift briefing assistant for a Caterpillar machine operator. The machine is stationary
(pre-shift, break, or post-shift) — this is the only time a longer conversation is allowed.
You can help with: walking through today's task queue and the foreman trade-off, why the operator was flagged
earlier (using the logged events provided), the recommended training, and drafting shift hand-off notes.
Ground every statement in the BRIEFING DATA below. If something isn't in the data, say you don't have it.
Be concise, friendly and practical; short paragraphs or bullets; no more than ~120 words per reply unless asked.
Never encourage working faster at the expense of safety."""


def briefing_context(conn, engine, machine_id, operator_id):
    from . import analytics
    op = rows(conn, "SELECT * FROM operator_profile WHERE operator_id=?", (operator_id,))
    cal = rows(conn, "SELECT soft_threshold, channel, trust_score, last_reason FROM operator_calibration WHERE operator_id=?", (operator_id,))
    recent = rows(conn, """SELECT timestamp, tier, message_key, message_text, trigger_reason, top_features, surfaced_to_operator FROM event_log
                           WHERE operator_id=? AND tier>=4 ORDER BY timestamp DESC LIMIT 12""", (operator_id,))
    for r in recent:
        from .ml.features import FEATURE_LABELS
        tops = json.loads(r.pop("top_features") or "[]")
        # Task-delay alerts come from the task-time model, not the risk model's contributors.
        if r["message_key"] == "T4_TASK_DELAY":
            r["reasons"] = ["the task-time model predicts this task will overrun"]
        else:
            r["reasons"] = [FEATURE_LABELS.get(f, f) for f, _ in tops]
    notes = rows(conn, "SELECT timestamp, note FROM shift_note WHERE machine_id=? ORDER BY timestamp DESC LIMIT 5", (machine_id,))
    return {
        "operator": op[0] if op else {"operator_id": operator_id},
        "arc_settings_for_operator": cal[0] if cal else None,
        "task_plan_and_tradeoff": analytics.tradeoff(engine, machine_id),
        "recent_alerts": recent,
        "training_recommendations": analytics.training_recommendations(conn, operator_id),
        "previous_handoff_notes": notes,
    }


def briefing_reply(conn, engine, machine_id, operator_id, messages: list) -> dict:
    ctx = briefing_context(conn, engine, machine_id, operator_id)
    if not llm_available():
        return {"text": _briefing_template(ctx, messages[-1]["content"] if messages else ""), "source": "template"}
    system = BRIEFING_SYSTEM + "\n\nBRIEFING DATA (JSON):\n" + json.dumps(ctx, default=str)
    try:
        resp = _call(system, _contents(messages), max_tokens=4096)
        txt = _text(resp)
        return {"text": txt or _briefing_template(ctx, ""), "source": "gemini" if txt else "template"}
    except Exception as e:
        return {"text": _briefing_template(ctx, ""), "source": "template", "error": str(e)[:200]}


def _briefing_template(ctx, question):
    """Offline fallback: keyword-routed answers, every fact read from the briefing data."""
    q = question.lower()
    t = ctx["task_plan_and_tradeoff"]
    alerts = ctx["recent_alerts"]
    tr = ctx["training_recommendations"]
    has = lambda *words: any(w in q for w in words)

    if has("why", "flag", "alert", "stop", "warn"):
        if not alerts:
            return "No tier-4 or tier-5 alerts are logged for you recently."
        lines = ["Your most recent alerts:"]
        for a in alerts[:3]:
            reason = ", ".join(a["reasons"][:2]) or a["trigger_reason"]
            lines.append(f"• {a['timestamp'][11:16]} — \"{a['message_text']}\" (because of {reason})")
        return "\n".join(lines)
    if has("train", "course", "module", "learn", "improve"):
        if not tr:
            return "No training gaps stand out right now."
        return "Recommended training:\n" + "\n".join(f"• {r['title']} ({r['minutes']} min) — {r['why']}" for r in tr)
    if has("plan", "task", "queue", "today", "finish", "late", "schedule", "foreman", "trade"):
        if not t:
            return "I don't have a task plan for this machine."
        left = [x for x in t["tasks"] if x["status"] != "done"]
        lines = [f"You have {len(left)} task(s) left. Predicted finish: "
                 f"{'%.0f min past' % t['overrun_min'] if t['overrun_min'] > 0 else 'within'} shift end."]
        for x in left:
            lines.append(f"• {x['task_type'].replace('_', ' ')} ({x['task_id']}, {x['status'].replace('_', ' ')}): "
                         f"planned {x['estimated_time_min']:.0f} min, likely {x['predicted_min']:.0f} min")
        rec = next((o for o in t["options"] if o["recommended"]), None)
        if rec:
            lines.append(f"Foreman's recommended option: {rec['label']} — {rec['detail']}.")
        push = next((o for o in t["options"] if o["id"] == "push"), None)
        if push:
            lines.append(f"Speeding up isn't recommended: {push['detail'].lower()}.")
        return "\n".join(lines)
    if has("setting", "threshold", "audio", "calibrat", "nudge"):
        s = ctx["arc_settings_for_operator"]
        if not s:
            return "No personal ARC settings recorded yet."
        return (f"ARC nudges you when risk reaches {s['soft_threshold']:.2f} (the STOP floor is fixed and never changes). "
                f"Channel: {'visual + audio' if s['channel'] == 'visual_audio' else 'visual only'}. Last change: {s['last_reason']}.")
    if has("note", "handoff", "hand-off", "hand off"):
        notes = ctx["previous_handoff_notes"]
        if not notes:
            return "No hand-off notes yet. To leave one, type: hand-off note: <your note>"
        return "Recent hand-off notes:\n" + "\n".join(f"• {n['timestamp'][:16].replace('T', ' ')} — {n['note']}" for n in notes)
    return ("I'm running offline (no Gemini API key), so I can answer these:\n"
            "• your plan / tasks / foreman options\n• why you were flagged\n• recommended training\n"
            "• your ARC settings\n• hand-off notes (type \"hand-off note: …\" to save one)")


# ---------------------------------------------------------------------------
# 3. Supervisor query agent (read-only SQL tool)
# ---------------------------------------------------------------------------
SUPERVISOR_SYSTEM = """You answer a site supervisor's questions about ARC (Attention & Risk Compression) data.
Answer ONLY from query results. Use the run_sql tool (SQLite, read-only) as many times as needed; never guess numbers.
Tables:
  event_log(event_id, timestamp ISO text, machine_id, operator_id, shift_id, tier 1-5, message_key, message_text,
            trigger_reason, risk_score_at_trigger, surfaced_to_operator 0/1, top_features JSON, source historical|live|demo)
  feedback_log(event_id, reaction_latency_s, corrective_action_taken, metric_returned_to_baseline, why_drawer_opened,
               ack_response acknowledged|dismissed|no_response, effectiveness_label effective|partial|ineffective|ignored, ack_latency_s)
  operator_profile(operator_id, name, tenure_months, skill_level, comprehension_profile, behavioral_archetype)
  operator_calibration(operator_id, soft_threshold, ambient_z, channel, trust_score, updated_at, last_reason)
  checklist_log(shift_id, operator_id, machine_id, field, operator_entered_value, sensor_ground_truth_value, mismatch_flag)
  shift(shift_id, date, start_ts, end_ts, status)   machine(machine_id, model, machine_type, machine_age_yrs)
Tier meaning: 5 immediate intervention, 4 one-line action card, 3 ambient icon, 2 supervisor-only log, 1 silent automation.
"Today" = the latest shift date. "This week" = last 7 days of shift dates. Give names with operator ids.
Reply in <= 150 words: a direct answer first, then 2-4 supporting bullets."""

def _sql_tool():
    return types.Tool(function_declarations=[types.FunctionDeclaration(
        name="run_sql",
        description="Run one read-only SQLite SELECT query against the ARC database and get up to 50 rows back as JSON.",
        parameters=types.Schema(type="OBJECT", required=["sql"],
                                properties={"sql": types.Schema(type="STRING", description="A single SELECT statement.")}),
    )])


def run_sql(sql: str):
    if not re.match(r"^\s*(select|with)\b", sql, re.I) or ";" in sql.strip().rstrip(";"):
        return {"error": "only a single SELECT statement is allowed"}
    ro = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        cur = ro.execute(sql)
        out = [dict(r) for r in cur.fetchmany(50)]
        return {"rows": out, "row_count_returned": len(out)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        ro.close()


def supervisor_query(conn, question: str) -> dict:
    if not llm_available():
        return {"text": _supervisor_template(conn, question), "source": "template", "queries": []}
    contents = _contents([{"role": "user", "content": question}])
    queries = []
    try:
        for _ in range(8):   # bounded tool loop
            resp = _call(SUPERVISOR_SYSTEM, contents, max_tokens=8192, tools=[_sql_tool()])
            calls = resp.function_calls or []
            if not calls:
                txt = _text(resp)
                if txt is None:
                    break                     # blocked / empty -> deterministic fallback
                return {"text": txt, "source": "gemini", "queries": queries}
            contents.append(resp.candidates[0].content)          # the model's turn, including its function calls
            parts = []
            for fc in calls:
                sql = (fc.args or {}).get("sql", "")
                res = run_sql(sql)
                queries.append(sql)
                parts.append(types.Part.from_function_response(name=fc.name, response=json.loads(json.dumps(res, default=str))))
            contents.append(types.Content(role="user", parts=parts))
        return {"text": _supervisor_template(conn, question), "source": "template", "queries": queries}
    except Exception as e:
        return {"text": _supervisor_template(conn, question), "source": "template", "error": str(e)[:200], "queries": queries}


def _supervisor_template(conn, q):
    """Keyword-routed canned queries — deterministic fallback, still 100% from the DB."""
    ql = q.lower()
    latest = conn.execute("SELECT MAX(date) FROM shift WHERE status='completed' OR shift_id IN (SELECT shift_id FROM event_log WHERE source!='historical')").fetchone()[0]
    today_ids = [r[0] for r in conn.execute("SELECT shift_id FROM shift WHERE date=?", (latest,))]
    if "today" in ql or "incident" in ql:
        ph = ",".join("?" * len(today_ids))
        r = rows(conn, f"""SELECT e.operator_id, p.name, e.machine_id, e.tier, e.message_text, e.timestamp FROM event_log e
                            JOIN operator_profile p USING(operator_id) WHERE e.shift_id IN ({ph}) AND e.tier>=4 AND e.surfaced_to_operator=1
                            ORDER BY e.tier DESC, e.timestamp""", today_ids)
        t5 = [x for x in r if x["tier"] == 5]
        lines = [f"{latest}: {len(t5)} tier-5 interventions and {len(r) - len(t5)} tier-4 cards surfaced."]
        for x in t5[:4]:
            lines.append(f"• {x['timestamp'][11:16]} {x['machine_id']} {x['name']} ({x['operator_id']}): {x['message_text']}")
        return "\n".join(lines)
    if "most" in ql and ("tier-4" in ql or "tier 4" in ql or "alerts" in ql):
        r = rows(conn, """SELECT e.operator_id, p.name, COUNT(*) n FROM event_log e JOIN operator_profile p USING(operator_id)
                          JOIN shift s USING(shift_id) WHERE e.tier=4 AND s.date >= date(?, '-6 days')
                          GROUP BY e.operator_id ORDER BY n DESC LIMIT 3""", (latest,))
        return f"Most tier-4 events in the last 7 days: {r[0]['name']} ({r[0]['operator_id']}) with {r[0]['n']}.\n" + \
            "\n".join(f"• {x['name']} ({x['operator_id']}): {x['n']}" for x in r[1:])
    if "ignore" in ql or "trust" in ql or "comprehension" in ql:
        r = rows(conn, "SELECT c.operator_id, p.name, c.trust_score, c.channel, c.last_reason FROM operator_calibration c JOIN operator_profile p USING(operator_id) ORDER BY c.trust_score LIMIT 3")
        return "Lowest trust scores:\n" + "\n".join(f"• {x['name']} ({x['operator_id']}): trust {x['trust_score']:.2f}, {x['channel']} — {x['last_reason']}" for x in r)
    if "checklist" in ql:
        r = rows(conn, "SELECT operator_id, COUNT(*) n FROM checklist_log WHERE mismatch_flag=1 GROUP BY operator_id ORDER BY n DESC LIMIT 3")
        return "Checklist mismatches by operator:\n" + "\n".join(f"• {x['operator_id']}: {x['n']}" for x in r)
    return ("Without a Gemini API key I can answer: \"summarize today's incidents\", \"which operator had the most tier-4 events "
            "this week\", \"who is ignoring messages\", \"checklist mismatches\". Set GEMINI_API_KEY in .env for free-form questions.")
