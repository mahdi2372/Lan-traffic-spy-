/* App shell: hash router, global search, live status, theme, scan button. */

import { api, connectStream, onEvent, streamState } from "./api.js";
import { el, mount, clear, fmt, toast, debounce, errorState } from "./ui.js";

import * as dashboard from "./pages/dashboard.js";
import * as devices from "./pages/devices.js";
import * as mapPage from "./pages/map.js";
import * as traffic from "./pages/traffic.js";
import * as router from "./pages/router.js";
import * as wifi from "./pages/wifi.js";
import * as ethernet from "./pages/ethernet.js";
import * as alerts from "./pages/alerts.js";
import * as history from "./pages/history.js";
import * as reports from "./pages/reports.js";
import * as settings from "./pages/settings.js";

const PAGES = {
  dashboard: { mod: dashboard, title: "Dashboard" },
  devices: { mod: devices, title: "Devices" },
  map: { mod: mapPage, title: "Network Map" },
  traffic: { mod: traffic, title: "Traffic" },
  router: { mod: router, title: "Router" },
  wifi: { mod: wifi, title: "Wi-Fi" },
  ethernet: { mod: ethernet, title: "Ethernet" },
  alerts: { mod: alerts, title: "Alerts" },
  history: { mod: history, title: "History" },
  reports: { mod: reports, title: "Reports" },
  settings: { mod: settings, title: "Settings" },
};

const ctx = {
  api,
  onEvent,
  navigate: (page, params) => {
    const q = params ? "?" + new URLSearchParams(params).toString() : "";
    location.hash = `#/${page}${q}`;
  },
  params: {},
};

let unmountCurrent = null;
let currentPage = null;

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [name, query] = h.split("?");
  return { page: PAGES[name] ? name : "dashboard", params: Object.fromEntries(new URLSearchParams(query || "")) };
}

async function route() {
  const { page, params } = parseHash();
  ctx.params = params;
  const content = document.getElementById("content");
  document.getElementById("page-title").textContent = PAGES[page].title;
  document.title = `LAN Watcher — ${PAGES[page].title}`;
  for (const a of document.querySelectorAll("#nav a")) {
    a.classList.toggle("active", a.dataset.page === page);
    if (a.dataset.page === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (typeof unmountCurrent === "function") {
    try { unmountCurrent(); } catch { /* page cleanup must not break navigation */ }
  }
  unmountCurrent = null;
  currentPage = page;
  content.focus({ preventScroll: true });
  try {
    unmountCurrent = await PAGES[page].mod.render(content, ctx) || null;
  } catch (err) {
    console.error(err);
    mount(content, errorState(err));
  }
}

/* ---------------- global search ---------------- */
function setupSearch() {
  const input = document.getElementById("global-search");
  const box = document.getElementById("search-results");
  let seq = 0;

  const close = () => { box.hidden = true; input.setAttribute("aria-expanded", "false"); };
  const run = debounce(async () => {
    const q = input.value.trim();
    const mySeq = ++seq;
    if (!q) return close();
    try {
      const data = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
      if (mySeq !== seq) return;
      clear(box);
      const results = data.results || [];
      if (!results.length) {
        box.append(el("div", { class: "empty" }, `No device matches “${q}”`));
      } else {
        for (const d of results) {
          box.append(el("div", {
            class: "sr-item", role: "option",
            onclick: () => { close(); input.value = ""; location.hash = `#/devices?focus=${encodeURIComponent(d.device_id)}`; },
          },
            el("span", { class: `dot ${d.online ? "ok" : "danger"}` }),
            el("strong", {}, fmt.deviceName(d)),
            el("span", { class: "mono dim" }, d.ipv4 || fmt.mac(d.mac)),
            el("span", { class: "pill neutral", style: "margin-left:auto" }, fmt.type(d.connection_type)),
          ));
        }
      }
      box.hidden = false;
      input.setAttribute("aria-expanded", "true");
    } catch (err) {
      clear(box).append(el("div", { class: "empty" }, err.what || "Search failed"));
      box.hidden = false;
    }
  }, 160);

  input.addEventListener("input", run);
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { input.value = ""; close(); input.blur(); }
    if (ev.key === "Enter") {
      const first = box.querySelector(".sr-item");
      if (first && !box.hidden) first.click();
    }
    if (ev.key === "ArrowDown" && !box.hidden) {
      ev.preventDefault();
      box.querySelector(".sr-item")?.focus?.();
    }
  });
  document.addEventListener("click", (ev) => {
    if (!box.contains(ev.target) && ev.target !== input) close();
  });
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault();
      input.focus();
      input.select();
    }
  });
}

/* ---------------- status bar / live bits ---------------- */
function setupShell() {
  const themeBtn = document.getElementById("btn-theme");
  const saved = localStorage.getItem("lw-theme") || "dark";
  document.documentElement.dataset.theme = saved;
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("lw-theme", next);
    toast(`${next === "dark" ? "Dark" : "Light"} theme enabled`, "ok", 1500);
  });

  document.getElementById("btn-scan").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await api.post("/api/scan", {});
      toast("Discovery scan started", "ok", 1800);
      setTimeout(() => { btn.disabled = false; }, 2500);
    } catch (err) {
      toast(`${err.what || "Scan failed"} — ${err.fix || "see Settings"}`, "danger", 5000);
      btn.disabled = false;
    }
  });

  // live status bar
  const sbStatus = document.getElementById("sb-status");
  const sbScan = document.getElementById("sb-scan");
  const sbBw = document.getElementById("sb-bw");
  setInterval(() => {
    sbStatus.textContent = streamState.connected ? "● live updates connected" : "○ reconnecting…";
    sbStatus.style.color = streamState.connected ? "var(--ok)" : "var(--warn)";
  }, 1000);

  onEvent("stats.bandwidth", (ev) => {
    const p = ev.payload || {};
    sbBw.textContent = `▲ ${fmt.bps(p.up_bps)}  ▼ ${fmt.bps(p.down_bps)}`;
  });
  onEvent("scan.finished", (ev) => {
    sbScan.textContent = `scan #${ev.payload?.scan ?? "?"}`;
  });
  onEvent("collector.status", (ev) => {
    const p = ev.payload || {};
    if (p.ok === false && p.error) {
      // non-fatal, visible in status bar + Settings
      sbScan.textContent = `collector '${p.name}' degraded`;
    }
  });
  onEvent("alert.new", () => refreshAlertBadge());

  window.addEventListener("hashchange", route);
}

async function refreshAlertBadge() {
  try {
    const data = await api.get("/api/alerts?state=unknown&limit=0");
    const open = (data.alerts || []).length;
    const badge = document.getElementById("nav-alert-badge");
    badge.hidden = open === 0;
    badge.textContent = String(open);
    badge.title = `${open} alert(s) awaiting triage`;
  } catch { /* badge is best-effort */ }
}

function refreshHealthPill() {
  api.get("/api/health").then((h) => {
    const pill = document.getElementById("health-pill");
    const overall = h.overall || "unknown";
    const cls = overall === "healthy" ? "ok" : overall === "degraded" ? "warn" : overall === "critical" ? "danger" : "neutral";
    pill.className = `pill ${cls}`;
    pill.textContent = `health: ${overall}${h.score != null ? ` (${h.score})` : ""}`;
    pill.title = (h.components || []).map((c) => `${c.name}: ${c.status}${c.detail ? " — " + c.detail : ""}`).join("\n");
  }).catch(() => {});
}

/* ---------------- boot ---------------- */
async function boot() {
  setupShell();
  setupSearch();
  connectStream();
  refreshAlertBadge();
  refreshHealthPill();
  setInterval(refreshHealthPill, 15000);
  try {
    const sys = await api.get("/api/system");
    document.getElementById("sb-version").textContent =
      `v${sys.version}${sys.demo_mode ? " · demo data" : ""} · ${sys.platform?.system || "?"}`;
  } catch (err) {
    document.getElementById("sb-version").textContent = "api unavailable";
  }
  await route();
}

boot();
