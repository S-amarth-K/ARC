// Supervisor dashboard: the full-analytics view. This is where everything the
// operator was spared lands — every tier, every held card, every silent action.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, openSocket, TIER_COLOR, TIER_NAME } from "../api.js";

const axis = { tick: { fill: "#8a8984", fontSize: 11 }, stroke: "#34332f" };
const tip = { contentStyle: { background: "#111", border: "1px solid #333", fontSize: 12 }, cursor: { fill: "#ffffff0d" } };
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
// Respect the OS "reduce motion" setting: no chart animations for those users.
const ANIM = !(typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

function Card({ title, sub, children, className = "" }) {
  return (
    <section className={`rounded-2xl bg-[#1a1a19] p-4 ${className}`}>
      <h3 className="text-sm font-semibold text-neutral-200">{title}</h3>
      {sub && <p className="text-xs text-neutral-500 mb-2">{sub}</p>}
      {children}
    </section>
  );
}

function Kpi({ label, value, sub }) {
  return (
    <div className="rounded-2xl bg-[#1a1a19] p-4">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="text-3xl font-semibold tabular-nums mt-1">{value}</div>
      {sub && <div className="text-xs text-neutral-500 mt-1">{sub}</div>}
    </div>
  );
}

const TierBadge = ({ tier }) => (
  <span className="px-1.5 py-0.5 rounded text-[11px] font-bold text-black whitespace-nowrap" style={{ background: TIER_COLOR[tier] }}>T{tier}</span>
);

// ---------------------------------------------------------------- overview
function Overview({ summary, trends }) {
  if (!summary || !trends) return <p className="text-neutral-500">Loading…</p>;
  const ew = summary.early_warning;
  const shifts = trends.per_shift.map((r) => ({ ...r, label: r.date.slice(5) }));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <Kpi label="Telemetry ticks evaluated" value={summary.telemetry_ticks.toLocaleString()} sub={`${summary.events_total.toLocaleString()} events logged`} />
        <Kpi label="Shown to operators" value={`${summary.surfaced_per_1000_ticks}`} sub={`tier 4-5 per 1,000 ticks (≈${(summary.surfaced_per_1000_ticks * 0.96).toFixed(1)} per 8-h machine-shift)`} />
        <Kpi label="Tier-5 interventions" value={summary.tier5} sub={`${summary.tier4_surfaced} tier-4 cards shown, ${summary.tier4 - summary.tier4_surfaced} held`} />
        <Kpi label="Early warning" value={pct(ew.coverage)} sub={`of ${ew.unsafe_events} unsafe events had a warning · median lead ${ew.median_lead_min} min`} />
        <Kpi label="Messages effective" value={pct(summary.effective_rate)} sub={`ack ${pct(summary.ack_rate)} · 'why' opened ${pct(summary.why_open_rate)}`} />
        <Kpi label="Checklist mismatches" value={summary.checklist_mismatches} sub={`of ${summary.checklist_fields} entries vs sensors`} />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="What reached the cab, per shift" sub="Tier 5 + tier-4 cards actually shown to operators">
          <div className="h-60">
            <ResponsiveContainer>
              <BarChart data={shifts} margin={{ left: -20, right: 8 }} barCategoryGap="25%">
                <CartesianGrid stroke="#34332f" vertical={false} />
                <XAxis dataKey="label" {...axis} /><YAxis {...axis} allowDecimals={false} />
                <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar isAnimationActive={ANIM} dataKey="tier5" name="Tier 5 intervene" stackId="a" fill="var(--tier5)" stroke="#1a1a19" strokeWidth={2} />
                <Bar isAnimationActive={ANIM} dataKey="tier4_surfaced" name="Tier 4 card shown" stackId="a" fill="var(--tier4)" stroke="#1a1a19" strokeWidth={2} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="What ARC handled without interrupting, per shift" sub="Logged for the supervisor — never shown in the cab (tier 3 is colour only)">
          <div className="h-60">
            <ResponsiveContainer>
              <BarChart data={shifts} margin={{ left: -20, right: 8 }} barCategoryGap="25%">
                <CartesianGrid stroke="#34332f" vertical={false} />
                <XAxis dataKey="label" {...axis} /><YAxis {...axis} allowDecimals={false} />
                <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar isAnimationActive={ANIM} dataKey="tier4_held" name="Tier 4 held (one-at-a-time)" stackId="b" fill="#9c5a3c" stroke="#1a1a19" strokeWidth={2} />
                <Bar isAnimationActive={ANIM} dataKey="tier3" name="Tier 3 ambient" stackId="b" fill="var(--tier3)" stroke="#1a1a19" strokeWidth={2} />
                <Bar isAnimationActive={ANIM} dataKey="tier2" name="Tier 2 supervisor log" stackId="b" fill="var(--tier2)" stroke="#1a1a19" strokeWidth={2} />
                <Bar isAnimationActive={ANIM} dataKey="tier1" name="Tier 1 silent automation" stackId="b" fill="var(--tier1)" stroke="#1a1a19" strokeWidth={2} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Message effectiveness per shift" sub="Share of tier-4/5 messages where the operator corrected AND the metric returned to baseline">
          <div className="h-52">
            <ResponsiveContainer>
              <LineChart data={shifts} margin={{ left: -20, right: 12 }}>
                <CartesianGrid stroke="#34332f" vertical={false} />
                <XAxis dataKey="label" {...axis} /><YAxis {...axis} domain={[0, 1]} tickFormatter={pct} />
                <Tooltip {...tip} formatter={(v) => pct(v)} />
                <Line isAnimationActive={ANIM} dataKey="effective_rate" name="effective" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 4, fill: "var(--series-1)", stroke: "#1a1a19", strokeWidth: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Tier 4-5 alerts by hour into shift" sub="Fatigue shows up late in the shift; the meal break at 4h resets it partly">
          <div className="h-52">
            <ResponsiveContainer>
              <BarChart data={trends.by_hour.map((r) => ({ ...r, label: `${r.hour}-${r.hour + 1}h` }))} margin={{ left: -20, right: 8 }}>
                <CartesianGrid stroke="#34332f" vertical={false} />
                <XAxis dataKey="label" {...axis} /><YAxis {...axis} allowDecimals={false} />
                <Tooltip {...tip} />
                <Bar isAnimationActive={ANIM} dataKey="alerts" name="tier 4-5 alerts" fill="var(--series-1)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
      <Card title="Message library usage" sub="Fixed, pre-written lines only — nothing is generated live in the cab">
        <table className="w-full text-sm">
          <thead className="text-xs text-neutral-500"><tr><th className="text-left py-1">Tier</th><th className="text-left">Key</th><th className="text-right">Logged</th><th className="text-right">Shown</th></tr></thead>
          <tbody>
            {trends.by_message.map((r) => (
              <tr key={r.message_key} className="border-t border-neutral-800">
                <td className="py-1"><TierBadge tier={r.tier} /></td><td className="font-mono text-xs">{r.message_key}</td>
                <td className="text-right tabular-nums">{r.n}</td><td className="text-right tabular-nums">{r.tier >= 3 ? r.surfaced : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- operators
function CalibrationChart({ operatorId }) {
  const [hist, setHist] = useState([]);
  useEffect(() => { api(`/api/calibration/history?operator_id=${operatorId}`).then(setHist); }, [operatorId]);
  const data = hist.map((h) => ({ t: h.timestamp.slice(5, 16).replace("T", " "), soft: h.soft_threshold, reason: h.reason, channel: h.channel }));
  return (
    <div>
      <div className="h-48">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ left: -20, right: 12 }}>
            <CartesianGrid stroke="#34332f" vertical={false} />
            <XAxis dataKey="t" {...axis} minTickGap={40} /><YAxis {...axis} domain={[0.3, 0.7]} />
            <Tooltip {...tip} formatter={(v, n, p) => [`${v} (${p.payload.channel})`, "soft threshold"]} labelFormatter={(l, p) => `${l} — ${p?.[0]?.payload.reason || ""}`} />
            <Line isAnimationActive={ANIM} type="stepAfter" dataKey="soft" stroke="var(--series-2)" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-neutral-500">Soft (tier-4) threshold over time. Lower = earlier nudges. The tier-5 hard floor never moves.</p>
      <ul className="mt-2 text-xs text-neutral-400 max-h-28 overflow-y-auto">
        {hist.slice(-6).reverse().map((h, i) => <li key={i}>{h.timestamp.slice(5, 16)} · {h.soft_threshold} · {h.channel} — {h.reason}</li>)}
      </ul>
    </div>
  );
}

function Operators({ ops }) {
  const [sel, setSel] = useState(null);
  if (!ops) return <p className="text-neutral-500">Loading…</p>;
  const cur = ops.find((o) => o.operator_id === sel);
  return (
    <div className="space-y-4">
      <Card title="Per-operator message effectiveness & comprehension" sub="Loop 1 = did it work · Loop 2 = did they understand. Click a row for calibration history and training.">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-neutral-500 text-left">
              <tr>
                <th className="py-2">Operator</th><th>Archetypes</th><th className="text-right">Effective</th><th className="text-right">Ignored</th>
                <th className="text-right">Ack</th><th className="text-right">Why opened</th><th className="text-right">Reaction</th>
                <th>Comprehension signal</th><th className="text-right">Soft thr.</th><th>Channel</th><th className="text-right">Trust</th>
              </tr>
            </thead>
            <tbody>
              {ops.map((o) => (
                <tr key={o.operator_id} onClick={() => setSel(o.operator_id)} className={`border-t border-neutral-800 cursor-pointer hover:bg-white/5 ${sel === o.operator_id ? "bg-white/5" : ""}`}>
                  <td className="py-2"><div className="font-medium">{o.name}</div><div className="text-xs text-neutral-500">{o.operator_id} · {o.tenure_months} mo · {o.skill_level}</div></td>
                  <td className="text-xs text-neutral-400">{o.behavioral_archetype.replaceAll("_", " ")}<br />{o.comprehension_profile.replaceAll("_", " ")}</td>
                  <td className="text-right tabular-nums">{pct(o.effective_rate)}</td>
                  <td className="text-right tabular-nums">{pct(o.ignored_rate)}</td>
                  <td className="text-right tabular-nums">{pct(o.ack_rate)}</td>
                  <td className="text-right tabular-nums">{pct(o.why_rate)}</td>
                  <td className="text-right tabular-nums">{o.avg_reaction_s}s</td>
                  <td className="text-xs">{o.comprehension_signal}</td>
                  <td className="text-right tabular-nums">{o.soft_threshold}</td>
                  <td className="text-xs">{o.channel === "visual_audio" ? "visual + audio" : "visual"}</td>
                  <td className="text-right tabular-nums">{o.trust_score?.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {cur && (
        <div className="grid lg:grid-cols-2 gap-4">
          <Card title={`${cur.name} — calibration history`} sub={cur.last_reason}><CalibrationChart operatorId={cur.operator_id} /></Card>
          <Card title={`${cur.name} — training-gap recommendations`} sub="Ranked by how much more often than the fleet this operator triggers each alert family">
            {cur.training.length === 0 ? <p className="text-sm text-neutral-400">No gaps stand out.</p> : cur.training.map((t) => (
              <div key={t.module} className="rounded-xl bg-neutral-900 p-3 mb-2">
                <div className="font-medium">{t.title} <span className="text-xs text-neutral-500">· {t.minutes} min</span></div>
                <div className="text-xs text-neutral-400">{t.why}</div>
              </div>
            ))}
            <div className="text-xs text-neutral-500 mt-2">{cur.tier5} tier-5 · {cur.tier4_surfaced}/{cur.tier4} tier-4 shown · {cur.shifts} shifts</div>
          </Card>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- event log
function EventDetail({ id, onClose }) {
  const [ev, setEv] = useState(null);
  useEffect(() => { api(`/api/events/${id}`).then(setEv); }, [id]);
  return (
    <div className="fixed inset-y-0 right-0 w-[440px] bg-[#141413] border-l border-neutral-800 p-5 overflow-y-auto z-30 slide-up">
      <button onClick={onClose} className="float-right text-neutral-500">✕</button>
      {!ev ? <p>Loading…</p> : (
        <>
          <div className="flex items-center gap-2"><TierBadge tier={ev.tier} /><span className="text-neutral-400 text-sm">{TIER_NAME[ev.tier]}</span></div>
          <h3 className="text-lg font-semibold mt-2">{ev.message_text}</h3>
          <p className="text-xs text-neutral-500 mt-1">#{ev.event_id} · {ev.timestamp.replace("T", " ")} · {ev.machine_id} · {ev.operator_id} · {ev.source}</p>
          <p className="text-sm mt-3"><span className="text-neutral-500">Trigger:</span> {ev.trigger_reason}</p>
          <p className="text-sm"><span className="text-neutral-500">Risk at trigger:</span> {ev.risk_score_at_trigger}</p>
          <p className="text-sm"><span className="text-neutral-500">Shown to operator:</span> {ev.surfaced_to_operator ? "yes" : "no (logged only)"}</p>
          {ev.why_labels?.length > 0 && <p className="text-sm mt-2"><span className="text-neutral-500">Top contributors:</span> {ev.why_labels.join(", ")}</p>}
          {ev.feedback?.[0] && (
            <div className="mt-3 rounded-lg bg-neutral-900 p-3 text-sm">
              <div className="font-semibold">Feedback: {ev.feedback[0].effectiveness_label}</div>
              <div className="text-neutral-400 text-xs">Loop 1 · corrective {ev.feedback[0].corrective_action_taken ? `yes (${ev.feedback[0].reaction_latency_s}s)` : "no"} · back to baseline {ev.feedback[0].metric_returned_to_baseline ? "yes" : "no"}</div>
              <div className="text-neutral-400 text-xs">Loop 2 · {ev.feedback[0].ack_response} · why opened {ev.feedback[0].why_drawer_opened ? "yes" : "no"}</div>
            </div>
          )}
          <h4 className="text-xs uppercase tracking-widest text-neutral-500 mt-4 mb-1">Signal snapshot ({ev.signal_snapshot_ref})</h4>
          <table className="w-full text-xs">
            <tbody>{Object.entries(ev.snapshot || {}).map(([k, v]) => (
              <tr key={k} className="border-t border-neutral-900"><td className="py-0.5 text-neutral-500">{k}</td><td className="text-right tabular-nums">{typeof v === "number" ? +v.toFixed(3) : String(v)}</td></tr>
            ))}</tbody>
          </table>
        </>
      )}
    </div>
  );
}

function EventLog({ meta, liveTick }) {
  const [f, setF] = useState({ min_tier: "", operator_id: "", source: "" });
  const [evs, setEvs] = useState([]);
  const [sel, setSel] = useState(null);
  useEffect(() => {
    const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    api(`/api/events?limit=300&${q}`).then(setEvs);
  }, [f, liveTick]);
  const sel_ = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const selectCls = "bg-neutral-900 rounded-lg px-3 py-2 text-sm";
  return (
    <Card title="Event log" sub="Everything the Arbitration Engine decided — including what the operator never saw">
      <div className="flex flex-wrap gap-2 mb-3">
        <select value={f.min_tier} onChange={sel_("min_tier")} className={selectCls}>
          <option value="">All tiers</option>{[5, 4, 3, 2].map((t) => <option key={t} value={t}>Tier ≥ {t}</option>)}
        </select>
        <select value={f.operator_id} onChange={sel_("operator_id")} className={selectCls}>
          <option value="">All operators</option>{meta?.operators.map((o) => <option key={o.operator_id} value={o.operator_id}>{o.name} ({o.operator_id})</option>)}
        </select>
        <select value={f.source} onChange={sel_("source")} className={selectCls}>
          <option value="">All sources</option><option value="historical">historical</option><option value="live">live</option><option value="demo">demo</option>
        </select>
      </div>
      <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-neutral-500 text-left sticky top-0 bg-[#1a1a19]">
            <tr><th className="py-2">Time</th><th>Tier</th><th>Machine</th><th>Operator</th><th>Message / reason</th><th className="text-right">Risk</th><th>Shown</th><th>Outcome</th></tr>
          </thead>
          <tbody>
            {evs.map((e) => (
              <tr key={e.event_id} onClick={() => setSel(e.event_id)} className="border-t border-neutral-800 hover:bg-white/5 cursor-pointer">
                <td className="py-1.5 text-xs tabular-nums text-neutral-400 whitespace-nowrap">{e.timestamp.slice(5, 16).replace("T", " ")}</td>
                <td><TierBadge tier={e.tier} /></td>
                <td>{e.machine_id}</td><td>{e.operator_id}</td>
                <td className="max-w-md"><div className="truncate">{e.message_text}</div><div className="text-xs text-neutral-500 truncate">{e.trigger_reason}</div></td>
                <td className="text-right tabular-nums">{e.risk_score_at_trigger?.toFixed(2)}</td>
                <td className="text-xs">{e.surfaced_to_operator ? "yes" : "—"}</td>
                <td className="text-xs">{e.effectiveness_label || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sel && <EventDetail id={sel} onClose={() => setSel(null)} />}
    </Card>
  );
}

function Checklist() {
  const [rowsData, setRows] = useState(null);
  useEffect(() => { api("/api/checklist").then(setRows); }, []);
  return (
    <Card title="Checklist vs sensor mismatches" sub="Pre-shift entries that disagree with machine sensors. Tier 2: logged for you, never shown in the cab.">
      {!rowsData ? <p>Loading…</p> : (
        <table className="w-full text-sm">
          <thead className="text-xs text-neutral-500 text-left"><tr><th className="py-2">Date</th><th>Shift</th><th>Operator</th><th>Machine</th><th>Field</th><th>Entered</th><th>Sensor</th></tr></thead>
          <tbody>{rowsData.map((r, i) => (
            <tr key={i} className="border-t border-neutral-800">
              <td className="py-1.5 text-neutral-400">{r.date}</td><td>{r.shift_id}</td><td>{r.name} <span className="text-neutral-500 text-xs">{r.operator_id}</span></td>
              <td>{r.machine_id}</td><td className="font-mono text-xs">{r.field}</td>
              <td className="text-[var(--serious)]">{r.operator_entered_value}</td><td className="text-[var(--good)]">{r.sensor_ground_truth_value}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- query agent
function QueryAgent() {
  const [q, setQ] = useState("");
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const ask = async (text) => {
    if (!text.trim()) return;
    setBusy(true); setQ("");
    const r = await api("/api/supervisor/query", { method: "POST", body: { question: text } }).catch((e) => ({ text: e.message }));
    setLog((l) => [{ q: text, ...r }, ...l]);
    setBusy(false);
  };
  return (
    <Card title="Ask the data" sub="Supervisor query agent · answers only from event_log / feedback_log via read-only SQL">
      <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. which operator had the most tier-4 events this week?" className="flex-1 bg-neutral-900 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 ring-[var(--cat-yellow)]" />
        <button disabled={busy} className="px-4 rounded-lg bg-[var(--cat-yellow)] text-black text-sm font-semibold">{busy ? "…" : "Ask"}</button>
      </form>
      <div className="flex flex-wrap gap-2 mt-2">
        {["Summarize today's incidents", "Which operator had the most tier-4 events this week?", "Who is ignoring messages?", "Checklist mismatches by operator"].map((s) => (
          <button key={s} onClick={() => ask(s)} className="text-xs px-2 py-1 rounded-full bg-neutral-800 hover:bg-neutral-700">{s}</button>
        ))}
      </div>
      <div className="mt-3 space-y-3 max-h-[50vh] overflow-y-auto">
        {log.map((x, i) => (
          <div key={i} className="rounded-xl bg-neutral-900 p-3 text-sm">
            <div className="text-neutral-500 text-xs mb-1">Q: {x.q}</div>
            <div className="whitespace-pre-wrap">{x.text}</div>
            {x.queries?.length > 0 && <details className="mt-1 text-xs text-neutral-500"><summary>SQL used ({x.queries.length})</summary>{x.queries.map((s, j) => <pre key={j} className="whitespace-pre-wrap mt-1">{s}</pre>)}</details>}
            {x.source === "template" && <div className="text-[10px] text-neutral-600 mt-1">offline template (no API key)</div>}
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- page
const TABS = [["overview", "Overview"], ["operators", "Operators & training"], ["events", "Event log"], ["checklist", "Checklist"]];

export default function Supervisor() {
  const [tab, setTab] = useState("overview");
  const [meta, setMeta] = useState(null);
  const [summary, setSummary] = useState(null);
  const [trends, setTrends] = useState(null);
  const [ops, setOps] = useState(null);
  const [feed, setFeed] = useState([]);
  const [liveTick, setLiveTick] = useState(0);

  const refresh = () => {
    api("/api/analytics/summary").then(setSummary);
    api("/api/analytics/trends").then(setTrends);
    api("/api/analytics/operators").then(setOps);
  };
  useEffect(() => {
    api("/api/meta").then(setMeta);
    refresh();
    let pending = null;
    const s = openSocket("/ws/supervisor", (m) => {
      if (m.type === "event" || m.type === "feedback" || m.type === "calibration" || m.type === "demo") {
        setFeed((f) => [m, ...f].slice(0, 30));
        clearTimeout(pending);
        pending = setTimeout(() => { setLiveTick((t) => t + 1); refresh(); }, 1500);   // debounce refreshes
      }
    });
    return () => s.close();
  }, []);

  const feedItems = useMemo(() => feed.filter((m) => m.type !== "event" || m.tier >= 2).slice(0, 12), [feed]);

  return (
    <div className="min-h-full bg-[#0f0f0e]">
      <header className="flex items-center justify-between px-6 py-4 border-b border-neutral-900">
        <div>
          <div className="text-xs uppercase tracking-[0.3em] text-[var(--cat-yellow)]">ARC · Supervisor</div>
          <h1 className="text-2xl font-semibold">Fleet safety & operator-assist dashboard</h1>
        </div>
        <div className="flex gap-3 text-sm">
          <Link to="/hud/M02?judge=1" className="px-3 py-2 rounded-lg bg-neutral-900 hover:bg-neutral-800">Operator HUD →</Link>
          <Link to="/" className="px-3 py-2 rounded-lg bg-neutral-900 hover:bg-neutral-800">Home</Link>
        </div>
      </header>
      <div className="grid xl:grid-cols-[1fr_380px] gap-4 p-6">
        <div className="min-w-0">
          <nav className="flex gap-1 mb-4">
            {TABS.map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} className={`px-4 py-2 rounded-lg text-sm ${tab === k ? "bg-[var(--cat-yellow)] text-black font-semibold" : "bg-neutral-900 text-neutral-300"}`}>{l}</button>
            ))}
          </nav>
          {tab === "overview" && <Overview summary={summary} trends={trends} />}
          {tab === "operators" && <Operators ops={ops} />}
          {tab === "events" && <EventLog meta={meta} liveTick={liveTick} />}
          {tab === "checklist" && <Checklist />}
        </div>
        <div className="space-y-4">
          <Card title="Live feed" sub="Streaming from the Arbitration Engine">
            {feedItems.length === 0 && <p className="text-xs text-neutral-500">Quiet. Run the demo or the simulator to see events stream in.</p>}
            <ul className="space-y-1.5">
              {feedItems.map((m, i) => (
                <li key={i} className="text-xs flex gap-2 items-start">
                  {m.type === "event" ? (<><TierBadge tier={m.tier} /><span><b>{m.machine_id}</b> {m.message_text} <span className="text-neutral-500">{m.surfaced_to_operator ? "· shown" : "· logged only"}</span></span></>)
                    : m.type === "feedback" ? <span className="text-neutral-300">↺ Feedback #{m.event_id}: <b>{m.effectiveness_label}</b> ({m.ack_response})</span>
                    : m.type === "calibration" ? <span className="text-[var(--series-1)]">⚙ {m.operator_id} recalibrated: soft {m.soft_threshold}, {m.channel}</span>
                    : <span className="text-[var(--cat-yellow)]">▶ {m.text}</span>}
                </li>
              ))}
            </ul>
          </Card>
          <QueryAgent />
        </div>
      </div>
    </div>
  );
}
