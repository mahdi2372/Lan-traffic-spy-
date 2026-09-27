/* History page: Device → Event → Timestamp → Details with range filters. */

import { el, mount, fmt, loadingState, errorState, emptyState } from "../ui.js";

const PRESETS = [
  ["today", "Today"],
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
  ["custom", "Custom range"],
  ["all", "Everything"],
];

export async function render(container, ctx) {
  const { api, params } = ctx;
  let preset = params.preset || "24h";
  let deviceId = params.device || "";

  const presetSel = el("select", { "aria-label": "Time range" },
    ...PRESETS.map(([v, label]) => el("option", { value: v, selected: v === preset }, label)));
  const fromInput = el("input", { type: "text", placeholder: "from: 2026-09-01T00:00:00Z", "aria-label": "Custom from", style: "width:210px", hidden: preset !== "custom" });
  const toInput = el("input", { type: "text", placeholder: "to: 2026-09-25T00:00:00Z", "aria-label": "Custom to", style: "width:210px", hidden: preset !== "custom" });
  const deviceSel = el("select", { "aria-label": "Filter by device" }, el("option", { value: "" }, "All devices"));

  const toolbar = el("div", { class: "row", style: "margin-bottom:10px" },
    presetSel, fromInput, toInput, deviceSel,
    el("span", { class: "faint", id: "hist-count" }));

  const panel = el("div", { class: "panel" });
  mount(container, toolbar, panel);
  mount(panel, loadingState(4));

  try {
    const devs = await api.get("/api/devices");
    for (const d of devs.devices || []) {
      deviceSel.append(el("option", { value: d.device_id, selected: d.device_id === deviceId },
        `${fmt.deviceName(d)} (${d.ipv4 || d.mac || d.device_id})`));
    }
  } catch { /* device filter degrades to All */ }

  presetSel.addEventListener("change", () => {
    preset = presetSel.value;
    fromInput.hidden = toInput.hidden = preset !== "custom";
    load();
  });
  deviceSel.addEventListener("change", () => { deviceId = deviceSel.value; load(); });
  fromInput.addEventListener("change", load);
  toInput.addEventListener("change", load);

  await load();

  async function load() {
    mount(panel, loadingState(4));
    const q = new URLSearchParams({ preset });
    if (deviceId) q.set("device", deviceId);
    if (preset === "custom") {
      if (fromInput.value) q.set("from", fromInput.value.trim());
      if (toInput.value) q.set("to", toInput.value.trim());
    }
    try {
      const data = await api.get(`/api/history?${q}`);
      draw(data.events || []);
    } catch (err) {
      mount(panel, errorState(err));
    }
  }

  function draw(events) {
    toolbar.querySelector("#hist-count").textContent = `${events.length} event(s)`;
    if (!events.length) {
      mount(panel, emptyState("No events in this range", "◷"));
      return;
    }
    mount(panel, el("ul", { class: "timeline", role: "table", "aria-label": "Device history timeline" },
      el("li", { style: "font-weight:700;border-bottom:1px solid var(--border2)" },
        el("span", {}, ""), el("span", {}, "Device → Event"), el("span", {}, "Details"), el("span", {}, "Timestamp")),
      ...events.map((e) => el("li", { role: "row", tabindex: "0" },
        el("span", { class: "tl-dot", title: e.source || "" }, dot(e.event_type)),
        el("span", {}, el("strong", {}, e.event_type), e.device_id ? el("span", { class: "faint mono", style: "font-size:10.5px" }, ` ${e.device_id.slice(0, 12)}`) : null),
        el("span", {}, e.label || JSON.stringify(e.details || {})),
        el("span", { class: "tl-ts", title: fmt.time(e.ts) }, fmt.time(e.ts)),
      )),
    ));
  }

  function dot(type) {
    const color = type === "new_device" ? "var(--warn)" : type === "offline" ? "var(--danger)"
      : type === "online" ? "var(--ok)" : type === "conflict" ? "var(--violet)" : "var(--accent)";
    return el("span", { style: `color:${color}` }, "●");
  }
}
