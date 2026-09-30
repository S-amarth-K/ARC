// Operator HUD. During active operation the operator sees: the ambient strip,
// and at most one card or one intervention. Everything else is locked away
// until the machine is stationary (pre-shift / break / post-shift).
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, openSocket } from "../api.js";
import { speak, unlockAudio } from "../components/audio.js";
import { AmbientStrip, Tier4Card, Tier5Overlay } from "../components/hud.jsx";
import { LockIcon, MicIcon } from "../components/icons.jsx";
import JudgePanel from "../components/JudgePanel.jsx";
import { BriefingChat, TasksScreen, TrainingScreen } from "../components/ShiftScreens.jsx";
import { listenOnce, voiceSupported } from "../components/voice.js";

const SCREENS = [["tasks", "Tasks"], ["training", "Training"], ["briefing", "Briefing"]];
const STATE_LABEL = { active: "Operating", break: "On break", pre_shift: "Pre-shift", post_shift: "Post-shift" };

function fmtElapsed(h) {
  const m = Math.round((h || 0) * 60);
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export default function OperatorHUD() {
  const { machineId } = useParams();
  const [params] = useSearchParams();
  const [hud, setHud] = useState(null);
  const [stage, setStage] = useState(null);
  const [loopLog, setLoopLog] = useState([]);
  const [whyFor, setWhyFor] = useState(null);
  const [screen, setScreen] = useState(null);
  const [judge, setJudge] = useState(params.get("judge") !== "0");
  const [demoBusy, setDemoBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [listening, setListening] = useState(false);
  const [notice, setNotice] = useState(null);         // short-lived feedback for buttons & voice
  const [lastHudAt, setLastHudAt] = useState(0);      // wall-clock time of the last telemetry update
  const [now, setNow] = useState(Date.now());
  const [speakWhy, setSpeakWhy] = useState(false);    // read the "why" answer aloud (when asked by voice)
  const sock = useRef(null);
  const noticeTimer = useRef(null);

  const flash = useCallback((text, kind = "info", ms = 4500) => {
    clearTimeout(noticeTimer.current);
    setNotice({ text, kind });
    noticeTimer.current = setTimeout(() => setNotice(null), ms);
  }, []);

  // Re-sync with the server's view (e.g. after a backend restart the session may be gone).
  const resync = useCallback(() => {
    api(`/api/hud/${machineId}`).then((h) => { setHud(h); if (h) setLastHudAt(Date.now()); }).catch(() => {});
  }, [machineId]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    sock.current = openSocket(`/ws/hud/${machineId}`, (m) => {
      if (m.type === "hud") { setHud(m); setLastHudAt(Date.now()); }
      else if (m.type === "demo") {
        setStage(m);
        if (m.stage === "start") { setLoopLog([]); setScreen(null); setDemoBusy(true); }
        if (m.stage === "break") setScreen("tasks");
        if (m.stage === "done") setDemoBusy(false);
      } else if (m.type === "feedback" || m.type === "calibration") setLoopLog((l) => [m, ...l].slice(0, 8));
    }, (ok) => { setConnected(ok); if (ok) resync(); });
    return () => sock.current?.close();
  }, [machineId, resync]);

  useEffect(() => {
    const onKey = (e) => { if (e.key.toLowerCase() === "d" && e.target.tagName !== "INPUT") setJudge((j) => !j); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const active = !hud || hud.shift_state === "active";
  useEffect(() => { if (active) setScreen(null); }, [active]);

  // Optional deep link, e.g. /hud/M02?screen=tasks — honoured only once the machine is stationary.
  const initialScreen = useRef(params.get("screen"));
  useEffect(() => {
    if (hud && !active && initialScreen.current) {
      setScreen(initialScreen.current);
      initialScreen.current = null;
    }
  }, [hud, active]);

  const focus = hud?.intervention || hud?.card;       // the ONE thing on screen, if any
  const ack = useCallback((response) => {
    if (!focus) return false;
    sock.current.send({ type: "ack", event_id: focus.event_id, response });
    setWhyFor(null);
    return true;
  }, [focus]);
  const why = useCallback((byVoice = false) => {
    if (!focus) return false;
    setSpeakWhy(byVoice);
    setWhyFor(focus.event_id);
    return true;
  }, [focus]);

  const setShiftState = async (state) => {
    try {
      await api(`/api/hud/${machineId}/shift_state`, { method: "POST", body: { state } });
      flash(state === "active"
        ? "Operating. Tasks, Training and Briefing are locked until the machine stops."
        : "On break. Tasks, Training and Briefing are unlocked.", "ok");
      return true;
    } catch (e) {
      flash(e.status === 404 ? "No live session for this machine. Press Run demo or start the simulator first." : `Couldn't change shift state: ${e.message}`, "error", 6000);
      resync();
      return false;
    }
  };

  const openScreen = (key) => {
    if (active) { flash(`${key[0].toUpperCase() + key.slice(1)} is locked while operating. Say "take a break" first.`, "error"); return; }
    setScreen(key);
  };

  // Voice: a small fixed command set. Every outcome gets visible (and short spoken) feedback.
  const talk = () => {
    unlockAudio();
    setListening(true);
    setNotice({ text: "Listening… say “why”, “got it”, “take a break”, “resume”, or “tasks”.", kind: "info" });
    listenOnce({
      onResult: async (cmd, said) => {
        const heard = `Heard “${said}”`;
        const reply = (text, kind = "ok") => { flash(`${heard} → ${text}`, kind); if (kind === "ok") speak(text); };
        if (cmd === "why") why(true) ? flash(`${heard} → explaining`, "ok") : reply("Nothing to explain right now. “Why” works when an alert is on screen.", "error");
        else if (cmd === "ack") ack("acknowledged") ? reply("Acknowledged.") : reply("No alert to acknowledge.", "error");
        else if (cmd === "dismiss") hud?.intervention ? reply("A stop can't be dismissed. Say “got it” to acknowledge.", "error")
          : ack("dismissed") ? reply("Dismissed.") : reply("No alert to dismiss.", "error");
        else if (cmd === "break") (await setShiftState("break")) && speak("Break started.");
        else if (cmd === "resume") (await setShiftState("active")) && speak("Resuming.");
        else if (["tasks", "training", "briefing"].includes(cmd)) {
          if (active) reply(`${cmd} is locked while operating.`, "error");
          else { setScreen(cmd); reply(`Opening ${cmd}.`); }
        } else reply("Not a command. Try “why”, “got it”, “take a break”, “resume”, or “tasks”.", "error");
      },
      onError: (msg) => flash(msg, "error", 7000),
      onEnd: () => { setListening(false); setNotice((n) => (n?.text.startsWith("Listening") ? null : n)); },
    });
  };

  const runDemo = async () => {
    unlockAudio();                // a user gesture: lets the browser play the audio clips
    setDemoBusy(true);
    await api("/api/demo/start", { method: "POST" }).catch(() => setDemoBusy(false));
  };

  // No telemetry for a while: say so plainly instead of showing a frozen clock.
  const stale = hud && now - lastHudAt > 10000;

  return (
    <div className="h-full flex bg-neutral-950 text-neutral-100 select-none">
      <main className="flex-1 flex flex-col min-w-0 relative">
        {/* ---- top bar: identity + ambient strip ---- */}
        <header className="flex items-center justify-between px-8 py-5 border-b border-neutral-900">
          <div>
            <div className="text-xs uppercase tracking-[0.3em] text-[var(--cat-yellow)]">ARC · {machineId}</div>
            <div className="text-2xl font-semibold">{hud ? `${hud.operator_id} · ${STATE_LABEL[hud.shift_state] || hud.shift_state}` : "Waiting for machine…"}</div>
            <div className="text-sm text-neutral-500 tabular-nums">{hud ? `${fmtElapsed(hud.shift_elapsed_h)} into shift · ${hud.timestamp.slice(11, 16)}` : connected ? "connected" : "connecting…"}</div>
          </div>
          <AmbientStrip ambient={hud?.ambient} />
        </header>

        {/* ---- centre: calm by default, one thing at a time ---- */}
        <section className="flex-1 grid place-items-center px-8 overflow-y-auto py-6">
          {screen && !active ? (
            <div className="w-full max-w-6xl self-start">
              {screen === "tasks" && <TasksScreen machineId={machineId} />}
              {screen === "training" && <TrainingScreen operatorId={hud.operator_id} />}
              {screen === "briefing" && <BriefingChat machineId={machineId} />}
            </div>
          ) : hud?.card && !hud?.intervention ? (
            <Tier4Card card={hud.card} onAck={ack} onWhy={() => why(false)} whyOpen={whyFor === hud.card.event_id} speakWhy={speakWhy} />
          ) : (
            <div className="text-center text-neutral-600">
              <div className="text-7xl font-light tabular-nums text-neutral-400">{hud ? hud.timestamp.slice(11, 16) : "--:--"}</div>
              <div className="mt-2 tracking-widest uppercase text-sm">{hud ? (active ? "all clear" : "machine stationary") : "no telemetry yet"}</div>
              {stale && (
                <div className="mt-4 text-sm text-neutral-500 normal-case">
                  No live telemetry: the machine isn't streaming (the demo has finished).
                  <br />Press <b>Run demo</b> (judge panel, <kbd>D</kbd>) or run <code className="text-neutral-300">python -m backend.simulator</code>.
                </div>
              )}
              {!hud && (
                <div className="mt-6 text-neutral-500 text-sm">
                  Press <b>Run demo</b> in the judge panel, or run <code className="text-neutral-300">python -m backend.simulator</code>.
                </div>
              )}
            </div>
          )}
        </section>

        {/* ---- feedback for buttons & voice commands ---- */}
        {notice && (
          <div role="status" className={`absolute bottom-24 left-1/2 -translate-x-1/2 z-30 max-w-2xl px-5 py-3 rounded-xl text-base shadow-lg slide-up ${
            notice.kind === "error" ? "bg-[#3a1616] text-[#ffb4b4] border border-[var(--critical)]"
              : notice.kind === "ok" ? "bg-[#132a13] text-[#b6f0b6] border border-[var(--good)]"
              : "bg-neutral-900 text-neutral-200 border border-neutral-700"}`}>
            {notice.text}
          </div>
        )}

        {/* ---- bottom bar: locked screens + voice + shift state ---- */}
        <footer className="flex items-center justify-between px-8 py-4 border-t border-neutral-900">
          <nav className="flex gap-2">
            {SCREENS.map(([key, label]) => (
              <button key={key} aria-disabled={active} onClick={() => (screen === key ? setScreen(null) : openScreen(key))}
                title={active ? "Available when the machine is stationary (pre-shift / break / post-shift)" : ""}
                className={`px-5 py-3 rounded-xl text-lg flex items-center gap-2 ${screen === key ? "bg-[var(--cat-yellow)] text-black" : "bg-neutral-900"} ${active ? "opacity-35 cursor-not-allowed" : ""}`}>
                {active && <LockIcon className="w-4 h-4" />}{label}
              </button>
            ))}
          </nav>
          <div className="flex items-center gap-3">
            {voiceSupported() && (
              <button onClick={talk} className={`px-4 py-3 rounded-xl flex items-center gap-2 ${listening ? "bg-[var(--critical)]" : "bg-neutral-900"}`} title="Push to talk: say “why” or “got it”">
                <MicIcon className="w-5 h-5" /> {listening ? "Listening…" : "Voice"}
              </button>
            )}
            {hud && (active
              ? <button onClick={() => setShiftState("break")} className="px-4 py-3 rounded-xl bg-neutral-900 text-neutral-300">Take break</button>
              : <button onClick={() => setShiftState("active")} className="px-4 py-3 rounded-xl bg-[var(--good)] text-black font-semibold">Resume operating</button>)}
            {!judge && <button onClick={() => setJudge(true)} className="px-3 py-3 rounded-xl bg-neutral-900 text-neutral-600 text-xs">judge</button>}
            <Link to="/" className="text-xs text-neutral-600 hover:text-neutral-400">home</Link>
          </div>
        </footer>

        {/* Tier 5 covers the whole operator view (the judge panel stays visible beside it). */}
        {hud?.intervention && (
          <Tier5Overlay iv={hud.intervention} onAck={ack} onWhy={() => why(false)} whyOpen={whyFor === hud.intervention.event_id} speakWhy={speakWhy} />
        )}
      </main>

      {judge && <JudgePanel hud={hud} stage={stage} loopLog={loopLog} onRunDemo={runDemo} demoBusy={demoBusy} onClose={() => setJudge(false)} />}
    </div>
  );
}
