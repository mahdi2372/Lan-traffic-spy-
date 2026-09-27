/* UI helpers: DOM builder, formatters, loading/empty/error states, modal, toast. */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

/* ---------------- formatters ---------------- */
export const fmt = {
  bps: (v) => {
    if (v == null) return "—";
    const units = ["bps", "kbps", "Mbps", "Gbps"];
    let x = Math.max(0, +v);
    for (const u of units) {
      if (x < 1000 || u === "Gbps") return `${x.toFixed(x < 10 ? 2 : x < 100 ? 1 : 0)} ${u}`;
      x /= 1000;
    }
    return `${x.toFixed(1)} Gbps`;
  },
  bytes: (v) => {
    if (v == null) return "—";
    let x = Math.max(0, +v);
    for (const u of ["B", "KB", "MB", "GB", "TB"]) {
      if (x < 1024 || u === "TB") return `${x.toFixed(x < 10 && u !== "B" ? 1 : 0)} ${u}`;
      x /= 1024;
    }
    return `${x.toFixed(1)} TB`;
  },
  ms: (v) => (v == null ? "—" : `${(+v).toFixed(1)} ms`),
  pct: (v) => (v == null ? "—" : `${(+v).toFixed(0)}%`),
  time: (iso) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso.endsWith && iso.endsWith("Z") ? iso : iso + "Z");
      return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch { return iso; }
  },
  ago: (iso) => {
    if (!iso) return "—";
    const d = new Date(iso.endsWith && iso.endsWith("Z") ? iso : iso + "Z");
    const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (s < 5) return "just now";
    if (s < 60) return `${s.toFixed(0)}s ago`;
    if (s < 3600) return `${(s / 60).toFixed(0)}m ago`;
    if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
    return `${(s / 86400).toFixed(1)}d ago`;
  },
  duration: (s) => {
    if (s == null) return "—";
    s = +s;
    if (s < 60) return `${s.toFixed(0)}s`;
    if (s < 3600) return `${(s / 60).toFixed(1)}m`;
    return `${(s / 3600).toFixed(1)}h`;
  },
  deviceName: (d) => d?.friendly_name || d?.hostname || d?.ipv4 || d?.mac || d?.device_id || "unknown",
  type: (t) => (t === "wifi" ? "Wi-Fi" : t === "ethernet" ? "Ethernet" : "Unknown"),
  mac: (v) => {
    if (!v) return "—";
    const hex = String(v).toLowerCase().replace(/[^0-9a-f]/g, "");
    return hex.length === 12 ? hex.match(/.{2}/g).join(":") : String(v);
  },
};

/* ---------------- states ---------------- */
export function loadingState(rows = 3) {
  return el("div", { class: "state-box", "aria-busy": "true" },
    el("div", { class: "faint", role: "status" }, "Loading…"),
    ...Array.from({ length: rows }, () => el("div", { class: "skeleton", style: "width:80%" })),
  );
}

export function emptyState(msg, icon = "◌") {
  return el("div", { class: "state-box" }, el("div", { class: "state-ico", "aria-hidden": "true" }, icon), el("div", {}, msg));
}

export function errorState(err) {
  const what = err?.what || err?.message || "Something went wrong";
  const why = err?.why || "The request failed or the data source is unavailable.";
  const fix = err?.fix || "Check the Settings page and collector status, then retry.";
  return el("div", { class: "error-box", role: "alert" },
    el("div", { class: "what" }, what),
    el("div", { class: "why" }, why),
    el("div", { class: "fix" }, fix),
  );
}

/* ---------------- toast ---------------- */
export function toast(message, kind = "info", ms = 3400) {
  const root = document.getElementById("toasts");
  const t = el("div", { class: `toast ${kind}`, role: "status" }, message);
  root.append(t);
  setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, ms);
}

/* ---------------- modal ---------------- */
let lastFocus = null;
export function modal(title, bodyNodes, { wide = false } = {}) {
  lastFocus = document.activeElement;
  const root = document.getElementById("modal-root");
  const close = () => {
    clear(root);
    document.removeEventListener("keydown", onKey);
    if (lastFocus?.focus) lastFocus.focus();
  };
  const onKey = (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); close(); }
    if (ev.key === "Tab") {
      const focusables = backdrop.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (!focusables.length) return;
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    }
  };
  const backdrop = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } },
    el("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title, style: wide ? "width:min(1080px,96vw)" : null },
      el("header", {}, el("h2", {}, title), el("button", { class: "close", "aria-label": "Close dialog", onclick: close }, "✕")),
      el("div", { class: "modal-body" }, bodyNodes),
    ),
  );
  clear(root).append(backdrop);
  document.addEventListener("keydown", onKey);
  backdrop.querySelector(".close").focus();
  return close;
}

/* ---------------- misc ---------------- */
export function statusPill(online) {
  return el("span", { class: `pill ${online ? "ok" : "danger"}`, title: online ? "Currently reachable" : "Not seen recently" },
    el("span", { class: `dot ${online ? "ok" : "danger"}` }), online ? "online" : "offline");
}

export function typePill(t) {
  const cls = t === "wifi" ? "accent" : t === "ethernet" ? "violet" : "neutral";
  return el("span", { class: `pill ${cls}` }, fmt.type(t));
}

export function attrPill(field, value, source, confidence) {
  const cls = confidence === "high" ? "ok" : confidence === "medium" ? "warn" : "neutral";
  return el("span", { class: `pill ${cls}`, title: `Source: ${source} · confidence: ${confidence}` }, `${field}: ${value}`);
}

export function mount(container, ...nodes) {
  clear(container).append(...nodes.flat(Infinity).filter(Boolean));
}

export function debounce(fn, ms = 200) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
