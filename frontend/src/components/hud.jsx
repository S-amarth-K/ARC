// Operator-facing HUD pieces. Design rule: at most ONE thing competes for the
// operator's attention at a time; everything else is colour-only ambient state.
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { playMessage, speak } from "./audio.js";
import { HealthIcon, PaceIcon, ShieldIcon, StopIcon } from "./icons.jsx";

const AMBIENT_COLOR = { green: "var(--good)", amber: "var(--warning)", red: "var(--critical)" };
const AMBIENT_ICONS = [
  ["safety", ShieldIcon, "Safety"],
  ["pace", PaceIcon, "Pace"],
  ["health", HealthIcon, "Machine"],
];

/** Three colour-only icons, always visible. Labels are tiny and only for accessibility/judges. */
export function AmbientStrip({ ambient }) {
  return (
    <div className="flex gap-3" role="status" aria-label="ambient status">
      {AMBIENT_ICONS.map(([key, Icon, label]) => {
        const state = ambient?.[key] || "green";
        return (
          <div key={key} className="flex flex-col items-center gap-1" title={`${label}: ${state}`}>
            <div
              className="w-14 h-14 rounded-2xl grid place-items-center transition-colors duration-500"
              style={{ background: AMBIENT_COLOR[state] + "22", color: AMBIENT_COLOR[state], border: `2px solid ${AMBIENT_COLOR[state]}` }}
            >
              <Icon className="w-8 h-8" />
            </div>
            <span className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** "Why" drawer content: bounded explanation from the backend (Gemini or template). */
export function WhyDrawer({ event, speakIt = false }) {
  const [why, setWhy] = useState(null);
  useEffect(() => {
    let live = true;
    api(`/api/why/${event.event_id}`, { method: "POST" })
      .then((r) => { if (!live) return; setWhy(r); if (speakIt) speak(r.text); })   // asked by voice -> answer by voice
      .catch(() => live && setWhy({ text: "Explanation unavailable." }));
    return () => { live = false; };
  }, [event.event_id]);
  return (
    <div className="mt-4 rounded-xl bg-black/40 p-4 text-left slide-up">
      {!why ? <div className="text-neutral-400 animate-pulse">…</div> : <p className="text-xl leading-snug">{why.text}</p>}
      {event.why_labels?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {event.why_labels.map((l) => (
            <span key={l} className="text-sm px-2 py-1 rounded-full bg-neutral-800 text-neutral-300">{l}</span>
          ))}
        </div>
      )}
      {why?.source && <div className="mt-2 text-[10px] text-neutral-600">source: {why.source}</div>}
    </div>
  );
}

/** Tier 4: one plain-language line; "why" collapsed by default. */
export function Tier4Card({ card, onAck, onWhy, whyOpen, speakWhy }) {
  const played = useRef(null);
  useEffect(() => {
    if (card.play_audio && played.current !== card.event_id) {
      played.current = card.event_id;
      playMessage(card.audio_ref, card.message_text);
    }
  }, [card]);
  return (
    <div className="w-full max-w-2xl rounded-3xl border-2 p-8 text-center slide-up" style={{ borderColor: "var(--serious)", background: "#2a1d16" }}>
      <p className="text-4xl font-semibold leading-tight">{card.message_text}</p>
      {whyOpen && <WhyDrawer event={card} speakIt={speakWhy} />}
      <div className="mt-8 flex justify-center gap-4">
        <button onClick={onWhy} className="px-6 py-4 rounded-2xl text-xl bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40" disabled={whyOpen}>
          Why?
        </button>
        <button onClick={() => onAck("acknowledged")} className="px-8 py-4 rounded-2xl text-xl font-semibold text-black" style={{ background: "var(--serious)" }}>
          Got it
        </button>
        <button onClick={() => onAck("dismissed")} className="px-6 py-4 rounded-2xl text-xl bg-neutral-900 text-neutral-400 hover:bg-neutral-800">
          Dismiss
        </button>
      </div>
    </div>
  );
}

/** Tier 5: fixed vocabulary + audio + high contrast. Cannot be dismissed — only acknowledged. */
export function Tier5Overlay({ iv, onAck, onWhy, whyOpen, speakWhy }) {
  useEffect(() => {
    playMessage(iv.audio_ref, iv.message_text);
    const t = setInterval(() => playMessage(iv.audio_ref, iv.message_text), 5000);   // repeat until acknowledged
    return () => clearInterval(t);
  }, [iv.event_id]);
  const [head, ...rest] = iv.message_text.split(". ");
  return (
    <div className="absolute inset-0 z-40 grid place-items-center hazard-stripes" role="alertdialog" aria-live="assertive">
      <div className="bg-black w-[min(92vw,900px)] rounded-3xl p-10 text-center border-8 border-[#d03b3b] pulse-ring">
        <StopIcon className="w-24 h-24 mx-auto text-[#ff4d4d]" />
        <div className="mt-2 text-8xl font-black tracking-tight text-[#ff4d4d]">{head.replace(".", "")}</div>
        <div className="mt-4 text-5xl font-bold text-white leading-tight">{rest.join(". ")}</div>
        {whyOpen && <WhyDrawer event={iv} speakIt={speakWhy} />}
        <div className="mt-10 flex justify-center gap-4">
          <button onClick={() => onAck("acknowledged")} className="px-12 py-6 rounded-2xl text-3xl font-black text-black bg-[#f5b700] hover:brightness-110">
            ACKNOWLEDGE
          </button>
          {!whyOpen && (
            <button onClick={onWhy} className="px-6 py-6 rounded-2xl text-2xl bg-neutral-800 text-white">Why?</button>
          )}
        </div>
      </div>
    </div>
  );
}
