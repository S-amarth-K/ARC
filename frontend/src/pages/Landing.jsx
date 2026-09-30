import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function Landing() {
  const [meta, setMeta] = useState(null);
  const [sessions, setSessions] = useState([]);
  useEffect(() => {
    api("/api/meta").then(setMeta).catch(() => {});
    const load = () => api("/api/sessions").then(setSessions).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);
  const live = new Set(sessions.map((s) => s.machine_id));
  return (
    <div className="min-h-full max-w-5xl mx-auto px-6 py-12">
      <div className="text-xs uppercase tracking-[0.3em] text-[var(--cat-yellow)]">Caterpillar · Smart Operator Assistant</div>
      <h1 className="text-5xl font-bold mt-2">ARC <span className="text-neutral-500 font-light">Attention & Risk Compression</span></h1>
      <p className="mt-4 text-lg text-neutral-400 max-w-3xl">
        The machine absorbs the complexity so the operator doesn't have to. Every signal is routed through one Arbitration
        Engine that decides whether to act silently, log for the supervisor, show an ambient icon, show one plain-language
        action, or intervene immediately — and everything is logged.
      </p>

      <div className="mt-10 grid md:grid-cols-2 gap-5">
        <Link to="/hud/M02?judge=1" className="rounded-3xl p-6 bg-[var(--cat-yellow)] text-black hover:brightness-105">
          <div className="text-sm font-semibold uppercase tracking-widest">Start here</div>
          <div className="text-3xl font-bold mt-1">Demo: Operator HUD (M02)</div>
          <div className="mt-2">Opens the cab view with the judge panel. Press <b>▶ Run demo</b>.</div>
        </Link>
        <Link to="/supervisor" className="rounded-3xl p-6 bg-neutral-900 hover:bg-neutral-800">
          <div className="text-sm font-semibold uppercase tracking-widest text-neutral-500">Back office</div>
          <div className="text-3xl font-bold mt-1">Supervisor dashboard</div>
          <div className="mt-2 text-neutral-400">Fleet trends, full event log, checklist mismatches, per-operator comprehension, training gaps, query agent.</div>
        </Link>
      </div>

      <h2 className="mt-12 text-sm uppercase tracking-widest text-neutral-500">Machines</h2>
      <div className="mt-3 grid sm:grid-cols-2 md:grid-cols-3 gap-3">
        {(meta?.machines || []).map((m) => (
          <Link key={m.machine_id} to={`/hud/${m.machine_id}`} className="rounded-2xl bg-neutral-900 hover:bg-neutral-800 p-4">
            <div className="flex items-center justify-between">
              <span className="font-semibold">{m.machine_id}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full ${live.has(m.machine_id) ? "bg-[var(--good)] text-black" : "bg-neutral-800 text-neutral-500"}`}>
                {live.has(m.machine_id) ? "live" : "idle"}
              </span>
            </div>
            <div className="text-sm text-neutral-400">{m.model}</div>
          </Link>
        ))}
      </div>
      {meta && (
        <p className="mt-8 text-xs text-neutral-600">
          Risk model holdout AUC {meta.models?.holdout_auc} · task model MAE {meta.task_model?.holdout_mae_min} min (planner {meta.task_model?.planner_estimate_mae_min}) ·
          AI agents: {meta.llm ? `live (${meta.llm_model})` : "offline templates (set GEMINI_API_KEY in .env)"}
        </p>
      )}
    </div>
  );
}
