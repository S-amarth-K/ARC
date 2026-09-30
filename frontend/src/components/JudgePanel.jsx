// Judge-facing debug panel. Shows what the operator does NOT see: the raw risk
// trajectory, thresholds, why the engine chose each tier, and the feedback loop.
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from "recharts";
import { TIER_COLOR, TIER_NAME } from "../api.js";

function RiskChart({ history, soft, hard }) {
  const data = history.map(([ts, risk]) => ({ t: ts.slice(11, 16), risk }));
  return (
    <div className="h-44">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="t" tick={{ fill: "#8a8984", fontSize: 10 }} interval="preserveStartEnd" minTickGap={30} stroke="var(--grid)" />
          <YAxis domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tick={{ fill: "#8a8984", fontSize: 10 }} stroke="var(--grid)" />
          <Tooltip contentStyle={{ background: "#111", border: "1px solid #333", fontSize: 12 }} formatter={(v) => [v.toFixed(3), "risk"]} />
          <ReferenceLine y={hard} stroke="var(--critical)" strokeDasharray="4 3" label={{ value: `hard floor ${hard}`, fill: "#e66767", fontSize: 10, position: "insideTopLeft" }} />
          <ReferenceLine y={soft} stroke="var(--serious)" strokeDasharray="4 3" label={{ value: `operator soft ${soft}`, fill: "#ec835a", fontSize: 10, position: "insideBottomLeft" }} />
          <Line type="monotone" dataKey="risk" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const Row = ({ k, v }) => (
  <div className="flex justify-between gap-2 text-xs py-0.5"><span className="text-neutral-500">{k}</span><span className="text-neutral-200 tabular-nums text-right">{v}</span></div>
);

export default function JudgePanel({ hud, stage, loopLog, onRunDemo, demoBusy, onClose }) {
  const d = hud?.debug;
  return (
    <aside className="w-[400px] shrink-0 h-full overflow-y-auto border-l border-neutral-800 bg-[#121211] p-4 text-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold tracking-wide text-[var(--cat-yellow)]">JUDGE PANEL</h2>
        <div className="flex gap-2">
          <button onClick={onRunDemo} disabled={demoBusy} className="px-3 py-1 rounded bg-[var(--cat-yellow)] text-black font-semibold disabled:opacity-50">
            {demoBusy ? "Demo running…" : "▶ Run demo"}
          </button>
          <button onClick={onClose} className="px-2 py-1 rounded bg-neutral-800 text-neutral-400" title="Hide (D)">✕</button>
        </div>
      </div>
      <p className="text-[11px] text-neutral-500 mt-1">Hidden from the operator. Toggle with <kbd>D</kbd>.</p>

      {stage && (
        <div className="mt-3 rounded-lg border border-[var(--cat-yellow)]/40 bg-[var(--cat-yellow)]/10 p-3 text-[13px]">
          <div className="text-[10px] uppercase tracking-widest text-[var(--cat-yellow)]">Demo · {stage.stage}</div>
          {stage.text}
          {stage.stage === "supervisor" && <a href="/supervisor" className="block mt-2 underline text-[var(--cat-yellow)]">Open supervisor dashboard →</a>}
        </div>
      )}

      {!d ? (
        <p className="mt-6 text-neutral-500">Waiting for telemetry… press <b>Run demo</b> or start the simulator.</p>
      ) : (
        <>
          <section className="mt-4">
            <div className="flex items-baseline justify-between">
              <h3 className="text-xs uppercase tracking-widest text-neutral-400">Risk trajectory · P(unsafe event ≤ 15 min)</h3>
              <span className="text-2xl font-bold tabular-nums">{(d.risk * 100).toFixed(0)}%</span>
            </div>
            <RiskChart history={d.risk_history} soft={d.soft_threshold} hard={d.hard_threshold} />
          </section>

          <section className="mt-3 rounded-lg bg-neutral-900 p-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold text-black" style={{ background: TIER_COLOR[d.tier] }}>TIER {d.tier}</span>
              <span className="text-neutral-300">{TIER_NAME[d.tier]}</span>
            </div>
            <div className="mt-1 text-xs text-neutral-400">{d.reason}</div>
          </section>

          <section className="mt-3">
            <h3 className="text-xs uppercase tracking-widest text-neutral-400 mb-1">Top contributing signals (SHAP)</h3>
            {d.top_features.length === 0 && <div className="text-xs text-neutral-500">none above noise</div>}
            {d.top_features.map((f) => (
              <div key={f.feature} className="mb-1">
                <div className="flex justify-between text-xs"><span>{f.label}</span><span className="tabular-nums text-neutral-400">+{f.contribution.toFixed(2)}</span></div>
                <div className="h-1.5 rounded bg-neutral-800"><div className="h-1.5 rounded bg-[var(--series-1)]" style={{ width: `${Math.min(100, f.contribution * 40)}%` }} /></div>
              </div>
            ))}
          </section>

          <section className="mt-3 grid grid-cols-2 gap-x-4">
            <div>
              <Row k="soft threshold" v={d.soft_threshold} />
              <Row k="channel" v={d.channel} />
              <Row k="ambient z" v={d.ambient_z} />
              <Row k="attention budget" v={`${d.attention_budget}/100`} />
            </div>
            <div>
              <Row k="jerk z" v={d.deviation.jerk_z} />
              <Row k="cycle z" v={d.deviation.cycle_z} />
              <Row k="idle z" v={d.deviation.idle_z} />
              <Row k="task delay p" v={d.task ? d.task.delay_prob : "—"} />
            </div>
          </section>
          <section className="mt-2 text-xs text-neutral-500">
            {Object.entries(d.signals).map(([k, v]) => `${k}=${v}`).join(" · ")}
          </section>

          <section className="mt-4">
            <h3 className="text-xs uppercase tracking-widest text-neutral-400 mb-1">Arbitration trail (incl. not shown)</h3>
            <div className="space-y-1">
              {d.decisions.filter((x) => x.tier > 1 || x.gate === "silent").slice(0, 10).map((x, i) => (
                <div key={i} className="text-xs flex gap-2">
                  <span className="tabular-nums text-neutral-600">{x.ts.slice(11, 16)}</span>
                  <span className="px-1 rounded text-black font-bold" style={{ background: TIER_COLOR[x.tier] }}>T{x.tier}</span>
                  <span className="text-neutral-300 truncate" title={x.reason}>{x.key || x.reason}</span>
                  <span className={x.surfaced ? "text-[var(--good)]" : "text-neutral-500"}>{x.surfaced ? "shown" : x.gate || "logged"}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      <section className="mt-4">
        <h3 className="text-xs uppercase tracking-widest text-neutral-400 mb-1">Feedback loops</h3>
        {loopLog.length === 0 && <div className="text-xs text-neutral-500">Scored 5 min (simulated) after each tier-4/5 message.</div>}
        {loopLog.map((m, i) =>
          m.type === "feedback" ? (
            <div key={i} className="text-xs mb-2 rounded bg-neutral-900 p-2">
              <div className="font-semibold">Event #{m.event_id}: <span className="uppercase">{m.effectiveness_label}</span></div>
              <div className="text-neutral-400">Loop 1 · corrective {m.corrective_action_taken ? `✓ in ${m.reaction_latency_s}s` : "✗"} · back to baseline {m.metric_returned_to_baseline ? "✓" : "✗"}</div>
              <div className="text-neutral-400">Loop 2 · {m.ack_response}{m.ack_latency_s != null ? ` in ${m.ack_latency_s}s` : ""} · why opened {m.why_drawer_opened ? "✓" : "✗"}</div>
            </div>
          ) : (
            <div key={i} className="text-xs mb-2 rounded border border-[var(--series-1)]/50 p-2">
              <div className="font-semibold">Calibration update · {m.operator_id}</div>
              <div className="text-neutral-400">soft → {m.soft_threshold}, channel → {m.channel}</div>
              <div className="text-neutral-500">{m.reason}</div>
            </div>
          )
        )}
      </section>
    </aside>
  );
}
