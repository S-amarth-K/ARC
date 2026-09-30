// Audio: pre-recorded clip for every fixed message; Web Speech API as live fallback.
let unlocked = false;
export function unlockAudio() { unlocked = true; }

export function speak(text) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace("STOP.", "Stop!"));
  u.rate = 1.0; u.pitch = 1.0;
  speechSynthesis.speak(u);
}

export function playMessage(audioRef, text) {
  let fellBack = false;
  const fallback = () => { if (!fellBack) { fellBack = true; speak(text); } };
  if (!audioRef) return fallback();
  const a = new Audio(audioRef);
  a.onerror = fallback;
  a.play().catch(fallback);
  return a;
}
