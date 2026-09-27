/* Dashboard: stat cards + live charts + health + latest alerts. */

import { el, mount, fmt, loadingState, errorState, emptyState, statusPill } from "../ui.js";
import { LineChart, sparkline } from "../charts.js";
import { SeriesBuffer } from "../state.js";
import { openDeviceDetail } from "./device_detail.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(4));

  let summary;
  try {
    summary = await api.get("/api/summary");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const bwHistory = new SeriesBuffer(180);
  try {
    const t = await api.get("/api/traffic?n=120");
    (t.bandwidth || []).forEach((p) => bwHistory.push(p));
  } catch { /* charts degrade to live-only */ }

  const cards = el("div", { class: "grid cols-4" });
  const chartsRow = el("div", { class: "grid cols-3" });
  const bottomRow = el("div", { class: "grid cols-2", style: "margin-top:14px" });

  const cTotal = statCard("Total devices", summary.counts.total, "neutral");
  const cOnline = statCard("Online", summary.counts.online, "ok");
  const cOffline = statCard("Offline", summary.counts.offline, "danger");
  const cNew = statCard("New devices today", summary.new_devices_today, "accent", "unknown devices seen for the first time");
  const cWifi = statCard("Wi-Fi devices", summary.counts.wifi, "accent", `${summary.counts.wifi_online || 0} online`);
  const cEth = statCard("Ethernet devices", summary.counts.ethernet, "violet", `${summary.counts.ethernet_online || 0} online`);
  const cUnk = statCard("Unknown link", summary.counts.unknown, "neutral", "connection type not established");
  const cAlerts = statCard("Alerts", summary.alerts.open, summary.alerts.open ? "warn" : "ok", `${summary.alerts.total} total`);
  cards.append(cTotal.el, cOnline.el, cOffline.el, cNew.el, cWifi.el, cEth.el, cUnk.el, cAlerts.el);

  const bwCard = chartCard("Total bandwidth");
  const upCard = chartCard("Upload");
  const downCard = chartCard("Download");
  chartsRow.append(bwCard.el, upCard.el, downCard.el);

  const chartTotal = new LineChart(bwCard.canvas, {
    series: [{ key: "total_bps", label: "total", color: "#22d3ee", fill: true }], unit: "bps", height: 150,
  });
  const chartUp = new LineChart(upCard.canvas, {
    series: [{ key: "up_bps", label: "upload", color: "#a78bfa", fill: true }], unit: "bps", height: 150,
  });
  const chartDown = new LineChart(downCard.canvas, {
    series: [{ key: "down_bps", label: "download", color: "#34d399", fill: true }], unit: "bps", height: 150,
  });

  const devActivityCard = chartCard("Device activity (online count)");
  const latencyCard = chartCard("Gateway latency");
  const lossCard = chartCard("Packet loss");
  chartsRow.append(devActivityCard.el, latencyCard.el, lossCard.el);
  const chartAct = new LineChart(devActivityCard.canvas, {
    series: [{ key: "online", label: "online", color: "#fbbf24", fill: true }], height: 150,
  });
  const chartLat = new LineChart(latencyCard.canvas, {
    series: [{ key: "gw_latency", label: "latency", color: "#22d3ee", fill: true }], unit: "ms", height: 150,
  });
  const chartLoss = new LineChart(lossCard.canvas, {
    series: [{ key: "loss", label: "loss", color: "#f87171", fill: true }], height: 150,
  });

  const healthPanel = el("div", { class: "panel" }, el("h2", {}, "Network health"));
  const alertsPanel = el("div", { class: "panel" }, el("h2", {}, "Latest alerts"));
  bottomRow.append(healthPanel, alertsPanel);

  mount(container,
    el("div", { class: "row between", style: "margin-bottom:12px" },
      el("div", { class: "row" },
        el("span", { class: "pill neutral", title: "Bandwidth measured from OS interface counters" },
          `▲ ${fmt.bps(summary.bandwidth.up_bps)}   ▼ ${fmt.bps(summary.bandwidth.down_bps)}`),
        el("span", { class: "pill neutral", title: "Total bytes transferred on this host since boot" },
          `Σ ${fmt.bytes(summary.bandwidth.bytes_sent_total + summary.bandwidth.bytes_recv_total)}`),
      ),
      el("span", { class: "faint" }, `scan #${summary.scan.count} · ${Math.round(summary.scan.last_ms)} ms`),
    ),
    cards, chartsRow, bottomRow,
  );

  // -------- live updates (event-driven; no full refreshes)
  const unsubs = [];
  unsubs.push(onEvent("stats", (ev) => {
    const c = ev.payload || {};
    cTotal.set(c.total ?? 0);
    cOnline.set(c.online ?? 0);
    cOffline.set(c.offline ?? 0);
    cWifi.set(c.wifi ?? 0, `${c.wifi_online || 0} online`);
    cEth.set(c.ethernet ?? 0, `${c.ethernet_online || 0} online`);
    cUnk.set(c.unknown ?? 0);
  }));
  unsubs.push(onEvent("stats.bandwidth", (ev) => {
    const p = ev.payload;
    bwHistory.push(p);
    chartTotal.setData(bwHistory.points);
    chartUp.setData(bwHistory.points);
    chartDown.setData(bwHistory.points);
  }));
  unsubs.push(onEvent("health", (ev) => {
    const h = ev.payload || {};
    renderHealth(healthPanel, h);
    const gw = (h.components || []).find((c) => c.name === "gateway_latency");
    histLat.push({ ts: Date.now() / 1000, gw_latency: gw?.latency_ms ?? 0, loss: gw?.loss_pct ?? 0 });
    chartLat.setData(histLat);
    chartLoss.setData(histLat);
  }));
  unsubs.push(onEvent("scan.finished", (ev) => {
    api.get("/api/summary").then((s) => {
      actHist.push({ ts: Date.now() / 1000, online: s.counts.online });
      chartAct.setData(actHist);
      cNew.set(s.new_devices_today, "unknown devices seen for the first time");
      cAlerts.set(s.alerts.open, `${s.alerts.total} total`);
    }).catch(() => {});
  }));
  unsubs.push(onEvent("alert.new", () => refreshAlerts()));
  unsubs.push(onEvent("device.updated", () => {}));

  const histLat = [];
  const actHist = [];
  renderHealth(healthPanel, summary.health || {});
  await refreshAlerts();
  chartTotal.setData(bwHistory.points);
  chartUp.setData(bwHistory.points);
  chartDown.setData(bwHistory.points);

  async function refreshAlerts() {
    try {
      const data = await api.get("/api/alerts?limit=6");
      mount(alertsPanel, el("h2", {}, "Latest alerts"),
        (data.alerts || []).length
          ? el("ul", { class: "timeline" }, ...data.alerts.map((a) => el("li", {},
              el("span", { class: "tl-dot", style: a.severity === "warning" ? "color:var(--warn)" : a.severity === "critical" ? "color:var(--danger)" : "" }, "●"),
              el("strong", {}, a.kind),
              el("span", {}, a.message),
              el("span", { class: "tl-ts" }, fmt.ago(a.ts)),
            )))
          : emptyState("No alerts — quiet network ✨", "✓"));
    } catch (err) {
      mount(alertsPanel, el("h2", {}, "Latest alerts"), errorState(err));
    }
  }

  return () => unsubs.forEach((u) => u());
}

function renderHealth(panel, h) {
  const comps = h.components || [];
  mount(panel, el("h2", {}, "Network health"),
    el("div", { class: "row", style: "margin-bottom:8px" },
      el("span", { class: `pill ${h.overall === "healthy" ? "ok" : h.overall === "degraded" ? "warn" : h.overall === "critical" ? "danger" : "neutral"}` }, h.overall || "unknown"),
      el("strong", {}, `score ${h.score ?? 0}/100`),
    ),
    el("ul", { class: "timeline" }, ...comps.map((c) => el("li", { style: "grid-template-columns:20px 140px 1fr" },
      el("span", { class: "tl-dot", style: c.status === "ok" ? "color:var(--ok)" : c.status === "down" ? "color:var(--danger)" : "color:var(--warn)" }, "●"),
      el("strong", { title: c.explanation || "" }, c.name),
      el("span", {}, `${c.status}${c.detail ? " — " + c.detail : ""}`, c.explanation ? el("div", { class: "faint", style: "font-size:11px" }, c.explanation) : null),
    ))));
}

function statCard(label, value, tone = "neutral", sub = "") {
  const val = el("div", { class: "value" }, String(value));
  const subEl = el("div", { class: "sub" }, sub);
  const card = el("div", { class: `stat-card ${tone}` },
    el("div", { class: "label" }, label), val, subEl);
  return { el: card, set: (v, s) => { val.textContent = String(v); if (s != null) subEl.textContent = s; } };
}

function chartCard(title) {
  const canvas = el("canvas", { role: "img", "aria-label": `${title} chart` });
  const card = el("div", { class: "panel chart-box" }, el("h2", {}, title), canvas);
  return { el: card, canvas };
}
