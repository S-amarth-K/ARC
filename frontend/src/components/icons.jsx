// Minimal inline icons (no icon font dependency).
const base = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" };

export const ShieldIcon = (p) => (
  <svg viewBox="0 0 24 24" {...base} {...p}><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3z" /></svg>
);
export const PaceIcon = (p) => (
  <svg viewBox="0 0 24 24" {...base} {...p}><circle cx="12" cy="13" r="8" /><path d="M12 13l4-3M10 2h4" /></svg>
);
export const HealthIcon = (p) => (
  <svg viewBox="0 0 24 24" {...base} {...p}><path d="M4 14h3l2-5 3 9 2-6 2 2h4" /><rect x="2" y="4" width="20" height="16" rx="3" /></svg>
);
export const LockIcon = (p) => (
  <svg viewBox="0 0 24 24" {...base} {...p}><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" /></svg>
);
export const MicIcon = (p) => (
  <svg viewBox="0 0 24 24" {...base} {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0014 0M12 18v3" /></svg>
);
export const StopIcon = (p) => (
  <svg viewBox="0 0 24 24" {...base} {...p}><path d="M8 2h8l6 6v8l-6 6H8l-6-6V8z" /><path d="M12 7v6M12 17h.01" /></svg>
);
