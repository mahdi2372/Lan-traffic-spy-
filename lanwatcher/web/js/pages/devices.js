/* Devices page: sortable/filterable inventory table with pin/rename/notes/export. */

import { el, mount, fmt, loadingState, errorState, emptyState, statusPill, typePill, toast, debounce } from "../ui.js";
import { DeviceTableStore } from "../state.js";
import { openDeviceDetail } from "./device_detail.js";

const COLS = [
  { key: "status", label: "Status", sortable: false },
  { key: "name", label: "Device", sortable: true },
  { key: "ipv4", label: "IP", sortable: true },
  { key: "mac", label: "MAC", sortable: true },
  { key: "vendor", label: "Vendor", sortable: true },
  { key: "type", label: "Type", sortable: true },
  { key: "upload", label: "Upload", sortable: true },
  { key: "download", label: "Download", sortable: true },
  { key: "last_seen", label: "Last Seen", sortable: true },
];

export async function render(container, ctx) {
  const { api, params } = ctx;
  const store = new DeviceTableStore();
  if (params.type) store.setType(params.type);
  if (params.status) store.setStatus(params.status);

  mount(container, loadingState(4));
  let rows;
  try {
    const data = await api.get("/api/devices");
    rows = data.devices;
  } catch (err) {
    mount(container, errorState(err));
    return;
  }
  store.setRows(rows);

  /* ---------- toolbar ---------- */
  const search = el("input", { type: "search", placeholder: "Filter devices…", "aria-label": "Filter devices", style: "width:220px" });
  const typeSel = el("select", { "aria-label": "Filter by connection type" },
    el("option", { value: "" }, "All links"),
    el("option", { value: "wifi", selected: store.type === "wifi" }, "Wi-Fi"),
    el("option", { value: "ethernet", selected: store.type === "ethernet" }, "Ethernet"),
    el("option", { value: "unknown", selected: store.type === "unknown" }, "Unknown"),
  );
  const statusSel = el("select", { "aria-label": "Filter by status" },
    el("option", { value: "" }, "Any status"),
    el("option", { value: "online", selected: store.status === "online" }, "Online"),
    el("option", { value: "offline", selected: store.status === "offline" }, "Offline"),
  );
  const groupSel = el("select", { "aria-label": "Group devices" },
    el("option", { value: "" }, "No grouping"),
    el("option", { value: "type" }, "Group by type"),
    el("option", { value: "category" }, "Group by category"),
  );
  const toolbar = el("div", { class: "row between", style: "margin-bottom:10px" },
    el("div", { class: "row" }, search, typeSel, statusSel, groupSel,
      el("span", { class: "faint", id: "dev-count" })),
    el("div", { class: "row" },
      el("button", { class: "btn", id: "btn-export", title: "Export selected devices as CSV" }, "⇩ Export selected"),
    ),
  );

  const tableWrap = el("div", { class: "table-wrap" });
  mount(container, toolbar, tableWrap);

  search.addEventListener("input", debounce(() => { store.setSearch(search.value); draw(); }, 140));
  typeSel.addEventListener("change", () => { store.setType(typeSel.value); draw(); });
  statusSel.addEventListener("change", () => { store.setStatus(statusSel.value); draw(); });
  groupSel.addEventListener("change", () => { store.setGroup(groupSel.value); draw(); });
  document.getElementById("btn-export")?.addEventListener; // bound below after mount
  toolbar.querySelector("#btn-export").addEventListener("click", exportSelected);

  async function exportSelected() {
    const ids = [...store.selected];
    if (!ids.length) return toast("Select one or more rows first (click the checkbox column)", "warn");
    try {
      const csv = await api.get(`/api/devices?export=1`); // placeholder shape
      // real export via report endpoint is whole-inventory; here per-selection CSV built client-side
      const lines = [["device_id", "name", "ipv4", "mac", "vendor", "type", "last_seen"].join(",")];
      for (const id of ids) {
        const d = store.rows.find((r) => r.device_id === id);
        if (!d) continue;
        lines.push([d.device_id, q(d.friendly_name || d.hostname), d.ipv4 || "", fmt.mac(d.mac), q(d.vendor), d.connection_type, d.last_seen].join(","));
      }
      const blob = new Blob([lines.join("\n")], { type: "text/csv" });
      const a = el("a", { href: URL.createObjectURL(blob), download: "lanwatcher-devices.csv" });
      document.body.append(a); a.click(); a.remove();
      toast(`Exported ${ids.length} device(s)`, "ok");
    } catch (err) {
      toast(err.what || "Export failed", "danger");
    }
  }

  function q(s) { return `"${String(s ?? "").replaceAll('"', '""')}"`; }

  function draw() {
    const groups = store.grouped();
    const count = store.visible().length;
    toolbar.querySelector("#dev-count").textContent = `${count} of ${store.rows.length} devices`;
    if (!count) {
      mount(tableWrap, emptyState(store.rows.length ? "No devices match the current filters" : "No devices discovered yet — hit ⟳ Scan now", "▤"));
      return;
    }
    mount(tableWrap, ...groups.map((g) => groupTable(g)));
  }

  function groupTable(group) {
    const thead = el("thead", {}, el("tr", {},
      el("th", { scope: "col" }, ""),
      ...COLS.map((c) => el("th", {
        scope: "col",
        class: c.sortable ? `sortable ${store.sort === c.key ? (store.order === "asc" ? "sort-asc" : "sort-desc") : ""}` : "",
        onclick: c.sortable ? () => { store.setSort(c.key === "type" ? "connection_type" : c.key); draw(); } : null,
        "aria-sort": store.sort === c.key ? (store.order === "asc" ? "ascending" : "descending") : "none",
      }, c.label)),
    ));
    const tbody = el("tbody", {});
    for (const d of group.rows) {
      const check = el("input", {
        type: "checkbox", "aria-label": `Select ${fmt.deviceName(d)}`,
        checked: store.selected.has(d.device_id),
        onclick: (ev) => { ev.stopPropagation(); store.toggleSelect(d.device_id); },
      });
      const tr = el("tr", {
        tabindex: "0",
        class: store.selected.has(d.device_id) ? "selected" : "",
        onclick: () => openDeviceDetail(d.device_id, api),
        onkeydown: (ev) => { if (ev.key === "Enter") openDeviceDetail(d.device_id, api); },
        ondblclick: () => openDeviceDetail(d.device_id, api),
      },
        el("td", {}, check),
        el("td", {}, d.pinned ? "📌 " : "", fmt.deviceName(d), d.is_self ? el("span", { class: "pill accent", style: "margin-left:6px" }, "this PC") : null),
        el("td", { class: "mono" }, d.ipv4 || "—", d.ipv6 ? el("div", { class: "faint mono", style: "font-size:10px" }, d.ipv6) : null),
        el("td", { class: "mono" }, fmt.mac(d.mac)),
        el("td", {}, d.vendor || "—"),
        el("td", {}, typePill(d.connection_type)),
        el("td", { class: "num mono", title: d.bytes_sent == null ? "No per-device source available (needs router telemetry or elevated OS stats)" : "" },
          d.bytes_sent == null ? "—" : fmt.bytes(d.bytes_sent)),
        el("td", { class: "num mono", title: d.bytes_recv == null ? "No per-device source available (needs router telemetry or elevated OS stats)" : "" },
          d.bytes_recv == null ? "—" : fmt.bytes(d.bytes_recv)),
        el("td", { class: "mono", title: fmt.time(d.last_seen) }, fmt.ago(d.last_seen)),
      );
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "p") { store.togglePin(d.device_id); draw(); toast(`Pin toggled for ${fmt.deviceName(d)}`, "ok", 1400); }
      });
      tbody.append(tr);
    }
    const table = el("table", { class: "data" },
      group.key ? el("caption", {}, `Group: ${group.key} (${group.rows.length})`) : null,
      thead, tbody);
    const wrap = el("div", { class: "table-wrap", style: "margin-bottom:10px" }, table);
    return wrap;
  }

  draw();

  if (params.focus) {
    const d = store.rows.find((r) => r.device_id === params.focus);
    if (d) openDeviceDetail(d.device_id, api);
  }

  const onUpdate = (ev) => refresh(ev?.detail);
  document.addEventListener("lw:device-updated", onUpdate);

  async function refresh(focusId) {
    try {
      const data = await api.get("/api/devices");
      store.setRows(data.devices);
      draw();
      if (focusId) {
        // keep context: nothing extra needed
      }
    } catch { /* keep stale table visible */ }
  }

  return () => document.removeEventListener("lw:device-updated", onUpdate);
}
