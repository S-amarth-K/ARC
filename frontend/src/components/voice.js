// Push-to-talk voice commands via the Web Speech API (Chrome/Edge).
// A small fixed command set, like the message library: no open-ended talk in the cab.
export function voiceSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

// Order matters: first match wins.
const COMMANDS = [
  ["why", /\bwhy\b|explain/],
  ["ack", /acknowledge|okay|\bok\b|got it|roger|copy/],
  ["dismiss", /dismiss|ignore/],
  ["break", /break|pause|stop working/],
  ["resume", /resume|start operating|back to work|continue/],
  ["tasks", /task|plan|schedule/],
  ["training", /training|train/],
  ["briefing", /brief/],
];

// Human-readable reasons for recognition errors (SpeechRecognitionErrorEvent.error).
const ERRORS = {
  "not-allowed": "Microphone blocked. Allow mic access for this site in the browser (address-bar mic icon).",
  "service-not-allowed": "Speech recognition isn't allowed in this browser. Use Chrome or Edge.",
  "audio-capture": "No microphone found.",
  "network": "Speech recognition needs an internet connection (Chrome/Edge send audio to their speech service).",
  "no-speech": "Didn't hear anything. Hold the button and try again.",
  "aborted": "Listening cancelled.",
};

/**
 * Listen for one utterance.
 * onResult(command, transcript) with command one of COMMANDS' names or "unknown".
 * onError(message) with a readable explanation. onEnd() always fires last.
 */
export function listenOnce({ onResult, onError, onEnd }) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    onError?.("Voice needs Chrome or Edge (this browser has no speech recognition).");
    onEnd?.();
    return null;
  }
  const r = new SR();
  r.lang = "en-US";
  r.interimResults = false;
  r.maxAlternatives = 3;
  r.onresult = (e) => {
    const said = Array.from(e.results[0]).map((x) => x.transcript.toLowerCase().trim());
    const all = said.join(" ");
    const hit = COMMANDS.find(([, re]) => re.test(all));
    onResult?.(hit ? hit[0] : "unknown", said[0]);
  };
  r.onerror = (e) => onError?.(ERRORS[e.error] || `Voice error: ${e.error}`);
  r.onend = () => onEnd?.();
  try {
    r.start();
  } catch (err) {
    onError?.(`Couldn't start the microphone: ${err.message}`);
    onEnd?.();
  }
  return r;
}
