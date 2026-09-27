/* Shared device profile dialog: identity, connectivity, activity, provenance. */

import { el, fmt, modal, mount, typePill, statusPill, toast, emptyState } from "../ui.js";

export async function openDeviceDetail(deviceId, api) {
  const close = modal("Device profile", el("div", {}, "Loading device…"));
  let data;
  try {
    data = await api.get(`/api/devices/${encodeURIComponent(deviceId)}`);
  } catch (err) {
    close();
    modal("Device profile", errorBox(err));
    return;
  }
  const d = data.device;

  // ---- tags (add / remove, capped at 40 chars server-side) ----
  const tagBox = el("div", { style: "display:flex;flex-wrap:wrap;gap:4px;align-items:center" });
  const renderTags = () => {
    tagBox.replaceChildren(
      ...(d.tags || []).map((t) => el("span", { class: "pill accent" },
        t,
        el("button", {
          class: "btn ghost", "aria-label": `Remove tag ${t}`,
          style: "margin-left:4px;padding:0 4px;line-height:1.2",
          onclick: async () => {
            try {
              await api.del(`/api/devices/${d.device_id}/tags/${encodeURIComponent(t)}`);
              d.tags = (d.tags || []).filter((x) => x !== t);
              renderTags();
              toast("Tag removed", "ok");
            } catch (err) { toast(err.what || err.message || "Could not remove tag", "error"); }
          },
        }, "×"),
      )),
      el("input", {
        type: "text", placeholder: "+ tag", maxlength: 40, "aria-label": "Add tag",
        style: "width:90px",
        onkeydown: async (ev) => {
          if (ev.key !== "Enter") return;
          const v = ev.target.value.trim();
          if (!v) return;
          try {
            await api.post(`/api/devices/${d.device_id}/tags`, { tag: v });
            d.tags = [...new Set([...(d.tags || []), v])].sort();
            renderTags();
            toast("Tag added", "ok");
          } catch (err) { toast(err.what || err.message || "Could not add tag", "error"); }
        },
      }),
    );
  };
  renderTags();

  const identity = el("dl", { class: "kv" },
    kv("Hostname", d.hostname), kv("IPv4", d.ipv4), kv("IPv6", d.ipv6),
    kv("MAC", fmt.mac(d.mac)), kv("Vendor", d.vendor), kv("Device type", d.category),
    kv("Friendly name", d.friendly_name),
    kv("First seen", fmt.time(d.first_seen)), kv("Last seen", `${fmt.time(d.last_seen)} (${fmt.ago(d.last_seen)})`),
    kv("Trust state", d.trust_state),
    kv("Tags", tagBox),
  );

  const totalOnline = (d.sessions || []).reduce((acc, s) => {
    const end = s.ended_at ? new Date(s.ended_at + "Z") : new Date();
    return acc + Math.max(0, (end - new Date(s.started_at + "Z")) / 1000);
  }, 0);

  const connectivity = el("dl", { class: "kv" },
    kv("Connection", fmt.type(d.connection_type)),
    kv("Local interface", d.interface_name),
    kv("Online duration (30d)", fmt.duration(totalOnline)),
    kv("Latency", fmt.ms(d.last_latency_ms), `avg ${fmt.ms(d.avg_latency_ms)}`),
    kv("Packet loss (probes)", fmt.pct(d.packet_loss_pct), `${d.probes_lost || 0}/${d.probes_sent || 0} probes lost`),
  );

  const trafficSummary = d.traffic_summary || {};
  const activity = el("div", {},
    el("dl", { class: "kv" },
      kv("Bytes sent", d.bytes_sent == null ? "no source" : fmt.bytes(d.bytes_sent), d.bytes_sent == null ? "per-device counters need router telemetry or elevated OS stats" : null),
      kv("Bytes received", d.bytes_recv == null ? "no source" : fmt.bytes(d.bytes_recv)),
      kv("Flows observed", trafficSummary.flows ?? 0, "metadata only — no payloads"),
    ),
    (trafficSummary.top_destinations || []).length
      ? el("div", {},
          el("h2", { style: "margin-top:10px" }, "Top destinations"),
          el("table", { class: "data" },
            el("thead", {}, el("tr", {}, el("th", {}, "Destination IP"), el("th", {}, "Connections"))),
            el("tbody", {}, ...trafficSummary.top_destinations.map((t) =>
              el("tr", {}, el("td", { class: "mono" }, t.dst_ip), el("td", { class: "num" }, t.n))))),
          el("h2", { style: "margin-top:10px" }, "Top destination ports"),
          el("table", { class: "data" },
            el("thead", {}, el("tr", {}, el("th", {}, "Port"), el("th", {}, "Protocol"), el("th", {}, "Connections"))),
            el("tbody", {}, ...trafficSummary.top_ports.map((t) =>
              el("tr", {}, el("td", { class: "mono" }, t.dst_port), el("td", {}, t.protocol), el("td", { class: "num" }, t.n))))),
        )
      : emptyState("No flows recorded for this device yet", "≋"),
  );

  const provenance = el("details", { class: "provenance" },
    el("summary", {}, "Field → Value → Source → Timestamp → Confidence"),
    el("table", { class: "data" },
      el("thead", {}, el("tr", {}, el("th", {}, "Field"), el("th", {}, "Value"), el("th", {}, "Source"), el("th", {}, "Timestamp"), el("th", {}, "Confidence"))),
      el("tbody", {}, ...(d.observations || []).map((o) =>
        el("tr", {},
          el("td", {}, o.field), el("td", { class: "mono" }, o.value ?? "—"), el("td", {}, o.source),
          el("td", { class: "mono" }, fmt.time(o.observed_at)), el("td", {}, o.confidence))))),
    Object.keys(d.conflicts || {}).length
      ? el("div", { class: "error-box", style: "margin-top:8px" },
          el("div", { class: "what" }, "Conflicting observations kept (never overwritten)"),
          el("div", { class: "why" }, `Fields with disagreeing values: ${Object.keys(d.conflicts).join(", ")}. Both values are preserved with their sources.`),
          el("div", { class: "fix" }, "Review the values below; set a friendly name/notes to record your conclusion."))
      : el("div", { class: "faint", style: "margin-top:6px" }, "No conflicting observations."),
  );

  const timeline = el("ul", { class: "timeline" },
    ...(d.timeline || []).slice(0, 30).map((ev) => el("li", {},
      el("span", { class: "tl-dot" }, "●"),
      el("strong", {}, ev.event_type),
      el("span", {}, ev.label || JSON.stringify(ev.details || {})),
      el("span", { class: "tl-ts" }, fmt.time(ev.ts)),
    )),
  );

  // edit form
  const nameInput = el("input", { type: "text", value: d.friendly_name || "", maxlength: 80, "aria-label": "Friendly name", style: "flex:1" });
  const notesInput = el("textarea", { "aria-label": "Notes", maxlength: 2000 }, d.notes || "");
  const trustSel = el("select", { "aria-label": "Trust state" },
    ...["unknown", "trusted", "ignore", "investigate"].map((s) => el("option", { value: s, selected: d.trust_state === s }, s)));
  const catInput = el("input", { type: "text", value: d.category || "unknown", maxlength: 40, "aria-label": "Device category", style: "width:130px" });

  const edit = el("div", { class: "panel", style: "margin-top:12px" },
    el("h2", {}, "User annotations"),
    el("div", { class: "row" }, el("label", {}, "Name ", nameInput), el("label", {}, "Category ", catInput), el("label", {}, "Trust ", trustSel)),
    el("label", { style: "display:block;margin-top:8px" }, "Notes"), notesInput,
    el("div", { class: "row", style: "margin-top:10px" },
      el("button", { class: "btn primary", onclick: async () => {
        try {
          await api.patch(`/api/devices/${encodeURIComponent(deviceId)}`, {
            friendly_name: nameInput.value, notes: notesInput.value, category: catInput.value, trust_state: trustSel.value,
          });
          toast("Device updated", "ok");
          close2();
          document.dispatchEvent(new CustomEvent("lw:device-updated", { detail: deviceId }));
        } catch (err) {
          toast(`${err.what || "Update failed"} — ${err.fix || ""}`, "danger", 5000);
        }
      } }, "Save"),
      el("button", { class: "btn danger", onclick: async () => {
        if (!confirm("Delete this device and its history? This cannot be undone.")) return;
        try {
          await api.del(`/api/devices/${encodeURIComponent(deviceId)}`);
          toast("Device deleted", "warn");
          close2();
          document.dispatchEvent(new CustomEvent("lw:device-updated", { detail: deviceId }));
        } catch (err) {
          toast(`${err.what || "Delete failed"}`, "danger", 5000);
        }
      } }, "Delete device"),
    ),
  );

  const body = el("div", {},
    el("div", { class: "row", style: "margin-bottom:10px" },
      el("strong", { style: "font-size:16px" }, fmt.deviceName(d)),
      statusPill(d.online), typePill(d.connection_type),
      d.pinned ? el("span", { class: "pill warn" }, "pinned") : null,
    ),
    el("div", { class: "grid cols-2" },
      el("div", { class: "panel" }, el("h2", {}, "Identity"), identity),
      el("div", { class: "panel" }, el("h2", {}, "Connectivity"), connectivity),
    ),
    el("div", { class: "panel", style: "margin-top:12px" }, el("h2", {}, "Network activity (metadata only)"), activity),
    el("div", { class: "panel", style: "margin-top:12px" }, el("h2", {}, "Provenance"), provenance),
    el("div", { class: "panel", style: "margin-top:12px" }, el("h2", {}, `Timeline (${(d.timeline || []).length} events)`), timeline),
    edit,
  );

  close();
  const close2 = modal("Device profile", body, { wide: true });
}

function kv(k, v, sub = null) {
  return el("div", { style: "display:contents" },
    el("dt", { title: sub || null }, k),
    el("dd", {}, v == null || v === "" ? "—" : v, sub ? el("span", { class: "faint", style: "font-family:var(--sans);font-size:11px" }, ` (${sub})`) : null),
  );
}

function errorBox(err) {
  return el("div", { class: "error-box" },
    el("div", { class: "what" }, err.what || "Failed to load device"),
    el("div", { class: "why" }, err.why || ""),
    el("div", { class: "fix" }, err.fix || ""),
  );
}
