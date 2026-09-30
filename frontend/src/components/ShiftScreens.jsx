// Screens only reachable when the machine is NOT operating (pre-shift, break,
// post-shift, or on request). The backend enforces this too (HTTP 423).
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

export function TasksScreen({ machineId }) {
  const [t, setT] = useState(null);
  const [err, setErr] = useState(null);
  const [decided, setDecided] = useState(null);
  useEffect(() => {
    api(`/api/tasks/${machineId}`).then(setT).catch((e) => setErr(e.message));
  }, [machineId]);
  if (err) return <p className="text-neutral-400">{err}</p>;
  if (!t) return <p className="text-neutral-500">Loading plan…</p>;
  const decide = async (o) => {
    await api(`/api/tasks/${machineId}/decide`, { method: "POST", body: { option_id: o.id } });
    setDecided(o.id);
  };
  return (
    <div className="grid lg:grid-cols-[1fr_1.1fr] gap-6">
      <div>
        <h3 className="text-lg font-semibold mb-2">Today's plan</h3>
        <p className="text-sm text-neutral-400 mb-3">
          Task-time model: predicted finish is{" "}
          <b className={t.overrun_min > 0 ? "text-[var(--serious)]" : "text-[var(--good)]"}>
            {t.overrun_min > 0 ? `${t.overrun_min.toFixed(0)} min past` : "within"} shift end
          </b>.
        </p>
        <div className="space-y-2">
          {t.tasks.map((x) => (
            <div key={x.task_id} className={`rounded-xl p-3 flex items-center justify-between ${x.status === "in_progress" ? "bg-neutral-800 ring-1 ring-[var(--cat-yellow)]" : "bg-neutral-900"} ${x.status === "done" ? "opacity-40" : ""}`}>
              <div>
                <div className="font-medium capitalize">{x.task_type.replaceAll("_", " ")} <span className="text-xs text-neutral-500">{x.task_id}</span></div>
                <div className="text-xs text-neutral-400">{x.status.replace("_", " ")}</div>
              </div>
              <div className="text-right text-sm tabular-nums">
                <div>plan {x.estimated_time_min.toFixed(0)} min</div>
                <div className={x.predicted_min > x.estimated_time_min * 1.15 ? "text-[var(--serious)]" : "text-neutral-400"}>
                  likely {x.predicted_min.toFixed(0)} min <span className="text-neutral-600">({x.pred_lo.toFixed(0)}–{x.pred_hi.toFixed(0)})</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h3 className="text-lg font-semibold mb-2">Foreman trade-off</h3>
        <p className="text-sm text-neutral-400 mb-3">ARC lays out the options; the foreman decides. "Go faster" is priced with the real risk model.</p>
        <div className="space-y-3">
          {t.options.map((o) => (
            <div key={o.id} className={`rounded-2xl p-4 border-2 ${o.recommended ? "border-[var(--good)]" : o.blocked ? "border-[var(--critical)]/60" : "border-neutral-800"} bg-neutral-900`}>
              <div className="flex items-center justify-between gap-3">
                <div className="text-lg font-semibold">{o.label}</div>
                {o.recommended && <span className="text-xs px-2 py-1 rounded-full bg-[var(--good)] text-black font-bold">RECOMMENDED</span>}
                {o.risk_delta > 0.01 && <span className="text-xs px-2 py-1 rounded-full bg-[var(--critical)] text-white font-bold">RISK +{(o.risk_delta * 100).toFixed(0)} pts</span>}
              </div>
              <div className="text-sm text-neutral-300 mt-1">{o.detail}</div>
              <div className="text-xs text-neutral-500 mt-1">Overrun after this option: {o.overrun_min.toFixed(0)} min {o.blocked_reason ? `· ${o.blocked_reason}` : ""}</div>
              <button onClick={() => decide(o)} disabled={!!decided}
                className={`mt-3 px-4 py-2 rounded-lg text-sm font-semibold ${decided === o.id ? "bg-[var(--good)] text-black" : "bg-neutral-800 hover:bg-neutral-700"} disabled:cursor-default`}>
                {decided === o.id ? "✓ Foreman approved — logged for supervisor" : "Foreman: approve"}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// One fully-written example module (per the brief: a single hardcoded example is fine).
const EXAMPLE_MODULE = {
  proximity: {
    title: "Working around people: crew zones & blind spots",
    steps: [
      "Before entering a crew zone, stop at the boundary marker and make eye contact with the spotter.",
      "Rear-camera view comes up automatically when something is within 15 m behind you — glance at it before every reverse.",
      "Inside the zone: walking pace, bucket low, no slewing over people.",
      "If you lose sight of a person you saw a moment ago: stop and wait. Don't guess.",
    ],
    check: "Where is the blind spot on a 320 excavator when slewing left?",
  },
};

export function TrainingScreen({ operatorId }) {
  const [recs, setRecs] = useState(null);
  const [open, setOpen] = useState(null);
  useEffect(() => { api(`/api/training/${operatorId}`).then(setRecs); }, [operatorId]);
  const mod = EXAMPLE_MODULE[open] || (open && { title: recs.find((r) => r.module === open)?.title, steps: ["Full module content is delivered by the site training team."] });
  return (
    <div className="grid lg:grid-cols-2 gap-6">
      <div>
        <h3 className="text-lg font-semibold mb-2">Recommended for you</h3>
        <p className="text-sm text-neutral-400 mb-3">Based on which alerts you trigger more often than the rest of the fleet.</p>
        {!recs ? <p className="text-neutral-500">Loading…</p> : recs.length === 0 ? <p className="text-neutral-400">Nothing stands out — nice work.</p> : recs.map((r) => (
          <button key={r.module} onClick={() => setOpen(r.module)} className="w-full text-left rounded-xl bg-neutral-900 hover:bg-neutral-800 p-4 mb-2">
            <div className="font-medium">{r.title}</div>
            <div className="text-xs text-neutral-400">{r.minutes} min · {r.why}</div>
          </button>
        ))}
        {recs && !recs.find((r) => r.module === "proximity") && (
          <button onClick={() => setOpen("proximity")} className="w-full text-left rounded-xl border border-neutral-800 p-4 text-neutral-400 text-sm">Example module: crew zones & blind spots →</button>
        )}
      </div>
      {mod && (
        <div className="rounded-2xl bg-neutral-900 p-5 slide-up">
          <h3 className="text-xl font-semibold">{mod.title}</h3>
          <ol className="mt-3 space-y-2 list-decimal pl-5 text-neutral-200">{mod.steps.map((s) => <li key={s}>{s}</li>)}</ol>
          {mod.check && <p className="mt-4 text-sm text-[var(--cat-yellow)]">Check yourself: {mod.check}</p>}
        </div>
      )}
    </div>
  );
}

export function BriefingChat({ machineId }) {
  const [msgs, setMsgs] = useState([{ role: "assistant", content: "Machine is stopped, so we can talk. Ask about your plan, why you were flagged, training, or say 'hand-off note: …'." }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  // Braces matter: scrollIntoView() returns a Promise in current Chrome, and an effect
  // must return nothing or a cleanup function — returning the Promise crashes React on unmount.
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  const send = async (text) => {
    if (!text.trim()) return;
    const next = [...msgs, { role: "user", content: text }];
    setMsgs(next); setInput(""); setBusy(true);
    try {
      if (/^hand-?off note:/i.test(text)) {
        await api("/api/briefing/note", { method: "POST", body: { machine_id: machineId, note: text.replace(/^hand-?off note:/i, "").trim() } });
        setMsgs([...next, { role: "assistant", content: "Saved to the shift hand-off log." }]);
      } else {
        // Drop the local greeting; the API expects the conversation to start with the user.
        const r = await api("/api/briefing", { method: "POST", body: { machine_id: machineId, messages: next.slice(1) } });
        setMsgs([...next, { role: "assistant", content: r.text, source: r.source }]);
      }
    } catch (e) {
      setMsgs([...next, { role: "assistant", content: e.message }]);
    }
    setBusy(false);
  };
  return (
    <div className="flex flex-col h-[60vh] max-w-3xl">
      <div className="flex-1 overflow-y-auto space-y-3 pr-2">
        {msgs.map((m, i) => (
          <div key={i} className={`rounded-2xl px-4 py-3 max-w-[85%] whitespace-pre-wrap ${m.role === "user" ? "ml-auto bg-[var(--cat-yellow)] text-black" : "bg-neutral-900"}`}>
            {m.content}{m.source === "template" && <div className="text-[10px] text-neutral-600 mt-1">offline template (no API key)</div>}
          </div>
        ))}
        {busy && <div className="text-neutral-500 animate-pulse">thinking…</div>}
        <div ref={end} />
      </div>
      <div className="flex flex-wrap gap-2 my-3">
        {["Walk me through my plan", "Why was I flagged earlier?", "What training do you recommend?"].map((q) => (
          <button key={q} onClick={() => send(q)} className="text-sm px-3 py-1 rounded-full bg-neutral-800 hover:bg-neutral-700">{q}</button>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="flex gap-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} className="flex-1 rounded-xl bg-neutral-900 px-4 py-3 outline-none focus:ring-2 ring-[var(--cat-yellow)]" placeholder="Ask the briefing assistant…" />
        <button className="px-5 rounded-xl bg-[var(--cat-yellow)] text-black font-semibold" disabled={busy}>Send</button>
      </form>
    </div>
  );
}
