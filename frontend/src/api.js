// Thin client for the FastAPI backend (proxied by Vite in dev, same origin in prod).
export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw Object.assign(new Error(data?.detail || res.statusText), { status: res.status, data });
  return data;
}

export function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

// Reconnecting WebSocket hook-free helper.
export function openSocket(path, onMessage, onStatus) {
  let ws, closed = false, timer;
  const connect = () => {
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => onStatus?.(true);
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onclose = () => { onStatus?.(false); if (!closed) timer = setTimeout(connect, 1500); };
  };
  connect();
  return {
    send: (msg) => ws?.readyState === 1 && ws.send(JSON.stringify(msg)),
    close: () => { closed = true; clearTimeout(timer); ws?.close(); },
  };
}

export const TIER_NAME = { 5: "Intervene", 4: "Action card", 3: "Ambient", 2: "Supervisor log", 1: "Silent / auto" };
export const TIER_COLOR = { 5: "var(--tier5)", 4: "var(--tier4)", 3: "var(--tier3)", 2: "var(--tier2)", 1: "var(--tier1)" };
