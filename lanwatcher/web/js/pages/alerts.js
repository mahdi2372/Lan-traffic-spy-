/* Alerts page: new-device queue with triage (trusted / unknown / ignore / investigate). */

import { el, mount, fmt, loadingState, errorState, emptyState, toast } from "../ui.js";
import { TRIAGE_STATES, triageTransition } from "../state.js";
import { openDeviceDetail } from "./device_detail.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(4));

  let data;
  try {
    data = await api.get("/api/alerts?limit=500");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const filterState = el("select", { "aria-label": "Filter alerts by triage state" },
    el("option", { value: "" }, "All states"),
    ...TRIAGE_STATES.map((s) => el("option", { value: s }, s)),
  );
  const filterKind = el("select", { "aria-label": "Filter alerts by kind" },
    el("option", { value: "" }, "All kinds"),
    el("option", { value: "new_device" }, "New device"),
    el("option", { value: "ip_change" }, "IP change"),
    el("option", { value: "device_online" }, "Device online"),
    el("option", { value: "device_offline" }, "Device offline"),
    el("option", { value: "health" }, "Health"),
  );

  const toolbar = el("div", { class: "row between", style: "margin-bottom:10px" },
    el("div", { class: "row" }, filterState, filterKind, el("span", { class: "faint", id: "alert-count" })),
    el("p", { class: "faint", style: "margin:0;font-size:12px" },
      "LAN Watcher never attacks, disconnects or blocks devices — triage is advisory only."),
  );

  const panel = el("div", { class: "panel" });
  mount(container, toolbar, panel);

  filterState.addEventListener("change", draw);
  filterKind.addEventListener("change", draw);
  draw();

  function filtered() {
    return (data.alerts || []).filter((a) =>
      (!filterState.value || a.state === filterState.value) &&
      (!filterKind.value || a.kind === filterKind.value));
  }

  function draw() {
    const rows = filtered();
    toolbar.querySelector("#alert-count").textContent = `${rows.length} alert(s)`;
    if (!rows.length) {
      mount(panel, emptyState("No alerts match — the network is quiet ✨", "✓"));
      return;
    }
    mount(panel, el("table", { class: "data" },
      el("thead", {}, el("tr", {},
        el("th", { scope: "col" }, "Time"), el("th", { scope: "col" }, "Kind"), el("th", { scope: "col" }, "Severity"),
        el("th", { scope: "col" }, "Device / details"), el("th", { scope: "col" }, "Triage"), el("th", { scope: "col" }, "Actions"))),
      el("tbody", {}, ...rows.map((a) => alertRow(a))),
    ));
  }

  function alertRow(a) {
    const d = a.details || {};
    const detailCell = el("td", {},
      el("div", {}, a.message),
      el("div", { class: "faint mono", style: "font-size:11px" },
        [d.ip, d.mac, d.vendor, d.hostname].filter(Boolean).join(" · ") || "—"),
      a.device_id ? el("a", { href: "#", class: "sm", onclick: (ev) => { ev.preventDefault(); openDeviceDetail(a.device_id, api); } }, "open profile ↗") : null,
    );

    const triageSel = el("select", { "aria-label": `Triage alert ${a.id}` },
      ...TRIAGE_STATES.map((s) => el("option", { value: s, selected: a.state === s }, s)));
    triageSel.addEventListener("change", async () => {
      const tr = triageTransition(a.state, triageSel.value);
      if (!tr.ok) { toast(tr.error, "warn"); triageSel.value = a.state; return; }
      try {
        await api.post(`/api/alerts/${a.id}/triage`, { state: tr.to });
        a.state = tr.to;
        toast(`Alert marked '${tr.to}'${tr.resolved ? " (resolved)" : ""}`, "ok", 1800);
        draw();
      } catch (err) {
        toast(err.what || "Triage failed", "danger");
        triageSel.value = a.state;
      }
    });

    const quick = el("td", {},
      el("div", { class: "row" },
        el("button", { class: "btn sm", title: "Mark as a known, expected device", onclick: () => { triageSel.value = "trusted"; triageSel.dispatchEvent(new Event("change")); } }, "✓ Trusted"),
        el("button", { class: "btn sm", title: "Keep as unknown, stay on the list", onclick: () => { triageSel.value = "unknown"; triageSel.dispatchEvent(new Event("change")); } }, "? Unknown"),
        el("button", { class: "btn sm", title: "Ignore future alerts for this device", onclick: () => { triageSel.value = "ignore"; triageSel.dispatchEvent(new Event("change")); } }, "∅ Ignore"),
        el("button", { class: "btn sm", title: "Flag for investigation", onclick: () => { triageSel.value = "investigate"; triageSel.dispatchEvent(new Event("change")); } }, "⚑ Investigate"),
      ));

    return el("tr", { tabindex: "0" },
      el("td", { class: "mono", title: fmt.time(a.ts) }, fmt.ago(a.ts)),
      el("td", {}, a.kind),
      el("td", {}, el("span", { class: `pill ${a.severity === "critical" ? "danger" : a.severity === "warning" ? "warn" : "neutral"}` }, a.severity)),
      detailCell,
      el("td", {}, triageSel, a.resolved_at ? el("div", { class: "faint", style: "font-size:10.5px" }, `resolved ${fmt.ago(a.resolved_at)}`) : null),
      quick,
    );
  }

  const unsubs = [onEvent("alert.new", async () => {
    try {
      data = await api.get("/api/alerts?limit=500");
      draw();
    } catch { /* keep list */ }
  })];
  return () => unsubs.forEach((u) => u());
}
