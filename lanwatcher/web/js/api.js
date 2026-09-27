/* API client: token-auth JSON fetch + Server-Sent Events stream. */

const TOKEN = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("lw-token") || "";
if (TOKEN) sessionStorage.setItem("lw-token", TOKEN);

export class ApiError extends Error {
  constructor(payload, status) {
    super(payload?.what || `HTTP ${status}`);
    this.what = payload?.what || `HTTP ${status}`;
    this.why = payload?.why || "";
    this.fix = payload?.fix || "";
    this.status = status;
  }
}

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: {
      "X-LANWatcher-Token": TOKEN,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    throw new ApiError(data?.error, res.status);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),
  reportUrl: (fmt, preset) => `/api/report/file?format=${encodeURIComponent(fmt)}&preset=${encodeURIComponent(preset)}&token=${encodeURIComponent(TOKEN)}`,
  download: async (fmt, preset) => {
    const res = await fetch(api.reportUrl(fmt, preset), { headers: { "X-LANWatcher-Token": TOKEN } });
    if (!res.ok) {
      let data = null;
      try { data = await res.json(); } catch { /* ignore */ }
      throw new ApiError(data?.error, res.status);
    }
    const blob = await res.blob();
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `lanwatcher-report.${fmt}`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
    return name;
  },
};

/* ---------------- SSE ---------------- */
const listeners = new Map(); // topic -> Set<fn>
let source = null;
let reconnectTimer = null;
export const streamState = { connected: false, lastEventTs: 0 };

export function onEvent(topic, fn) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(fn);
  return () => listeners.get(topic)?.delete(fn);
}

function emit(msg) {
  streamState.lastEventTs = Date.now();
  for (const topic of [msg.topic, "*"]) {
    for (const fn of listeners.get(topic) || []) {
      try { fn(msg); } catch (err) { console.error("event handler failed", err); }
    }
  }
}

export function connectStream() {
  if (source) return;
  source = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  source.onopen = () => { streamState.connected = true; };
  source.onmessage = (ev) => {
    try { emit(JSON.parse(ev.data)); } catch { /* skip malformed */ }
  };
  source.onerror = () => {
    streamState.connected = false;
    source?.close();
    source = null;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectStream, 3000);
  };
}
