/* Traffic page: bandwidth charts + flow metadata table. No payloads, ever. */

import { el, mount, fmt, loadingState, errorState, emptyState, typePill } from "../ui.js";
import { LineChart } from "../charts.js";
import { SeriesBuffer } from "../state.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(3));

  let data;
  try {
    data = await api.get("/api/traffic?n=180");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const series = new SeriesBuffer(360);
  (data.bandwidth || []).forEach((p) => series.push(p));

  const combinedCard = el("div", { class: "panel chart-box" },
    el("h2", {}, "Total / Upload / Download bandwidth"),
    el("canvas", { role: "img", "aria-label": "Bandwidth over time" }));
  const chart = new LineChart(combinedCard.querySelector("canvas"), {
    series: [
      { key: "total_bps", label: "total", color: "#22d3ee", fill: true },
      { key: "up_bps", label: "upload", color: "#a78bfa" },
      { key: "down_bps", label: "download", color: "#34d399" },
    ],
    unit: "bps", height: 220,
  });
  chart.setData(series.points);

  const stats = el("div", { class: "row", style: "margin:12px 0" },
    el("span", { class: "pill accent" }, `▲ ${fmt.bps(data.latest?.up_bps ?? 0)}`),
    el("span", { class: "pill ok" }, `▼ ${fmt.bps(data.latest?.down_bps ?? 0)}`),
    el("span", { class: "pill neutral", title: "Cumulative counters since host boot" },
      `total sent ${fmt.bytes(data.latest?.bytes_sent_total)} · recv ${fmt.bytes(data.latest?.bytes_recv_total)}`),
  );

  const note = el("div", { class: "panel", style: "margin-bottom:12px" },
    el("h2", {}, "Privacy"),
    el("p", { class: "dim", style: "margin:0" },
      data.note || "Flow metadata only. Bytes appear only where OS/router telemetry provides them.",
      " LAN Watcher never captures packet payloads, credentials, keystrokes or personal content."));

  const flowsPanel = el("div", { class: "panel" }, el("h2", {}, `Flows (metadata)`, el("span", { class: "count" }, ` ${(data.flows || []).length}`)));

  function flowTable(flows) {
    if (!flows.length) return emptyState("No flows observed yet", "≋");
    return el("div", { class: "table-wrap" }, el("table", { class: "data" },
      el("caption", {}, "5-tuple metadata with optional byte counts — never payload contents"),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col" }, "Time"), el("th", { scope: "col" }, "Source"), el("th", { scope: "col" }, "Destination"),
        el("th", { scope: "col" }, "Proto"), el("th", { scope: "col" }, "State"),
        el("th", { scope: "col", title: "Only populated when a supported OS/router source measures bytes" }, "Bytes"),
        el("th", { scope: "col" }, "Duration"))),
      el("tbody", {}, ...flows.slice(0, 200).map((f) => el("tr", { tabindex: "0" },
        el("td", { class: "mono" }, fmt.time(f.ts)),
        el("td", { class: "mono" }, `${f.src_ip || "?"}:${f.src_port ?? "*"}`),
        el("td", { class: "mono" }, f.dst_ip ? `${f.dst_ip}:${f.dst_port ?? "*"}` : "—"),
        el("td", {}, (f.protocol || "").toUpperCase()),
        el("td", {}, f.state || "—"),
        el("td", { class: "num mono" }, f.bytes == null ? "—" : fmt.bytes(f.bytes)),
        el("td", { class: "num mono" }, f.duration_s == null ? "—" : fmt.duration(f.duration_s)),
      ))),
    ));
  }
  mount(flowsPanel, el("h2", {}, "Flows (metadata)", el("span", { class: "count" }, ` ${(data.flows || []).length}`)), flowTable(data.flows || []));

  mount(container,
    data.available === false
      ? el("div", { class: "error-box", style: "margin-bottom:12px" },
          el("div", { class: "what" }, "Traffic metadata is limited"),
          el("div", { class: "why" }, data.note || "Interface counters unavailable on this system."),
          el("div", { class: "fix" }, "Check Settings → Traffic and collector status."))
      : note,
    combinedCard, stats, flowsPanel);

  const unsub = onEvent("stats.bandwidth", (ev) => {
    series.push(ev.payload);
    chart.setData(series.points);
    stats.querySelector(".pill.accent").textContent = `▲ ${fmt.bps(ev.payload.up_bps)}`;
    stats.querySelector(".pill.ok").textContent = `▼ ${fmt.bps(ev.payload.down_bps)}`;
  });
  const unsub2 = onEvent("flows", async () => {
    try {
      const fresh = await api.get("/api/traffic?n=1");
      mount(flowsPanel, el("h2", {}, "Flows (metadata)", el("span", { class: "count" }, ` ${(fresh.flows || []).length}`)), flowTable(fresh.flows || []));
    } catch { /* keep table */ }
  });
  return () => { unsub(); unsub2(); };
}
