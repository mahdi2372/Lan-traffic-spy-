/* Router page: gateway facts, WAN status, DHCP leases, DNS, integration status. */

import { el, mount, fmt, loadingState, errorState, emptyState, toast } from "../ui.js";
import { openDeviceDetail } from "./device_detail.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(4));

  let data;
  try {
    data = await api.get("/api/router");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const r = data.router || {};
  const sys = data.system || {};
  const integ = data.integration || {};

  const facts = el("dl", { class: "kv" },
    kv("Router / gateway IP", r.router_ip || r.gateway_ip),
    kv("Gateway IP", r.gateway_ip),
    kv("Router hostname", r.hostname),
    kv("Model / vendor", [r.model, r.vendor].filter(Boolean).join(" · ")),
    kv("LAN subnet", r.lan_subnet),
    kv("DHCP range", r.dhcp_range_start ? `${r.dhcp_range_start} – ${r.dhcp_range_end || "?"}` : "not available"),
    kv("DNS servers", r.dns_servers),
    kv("IPv6 gateway", r.ipv6_gateway),
    kv("IPv6 WAN", r.ipv6_wan),
  );

  const wan = el("dl", { class: "kv" },
    kv("WAN status", r.wan_status || r.physical_link),
    kv("WAN IP", r.wan_ip),
    kv("WAN uptime", r.uptime_s != null ? fmt.duration(r.uptime_s) : r.wan_uptime_s != null ? fmt.duration(r.wan_uptime_s) : "not available"),
    kv("WAN link rate", r.wan_upstream_bps ? `▲ ${fmt.bps(r.wan_upstream_bps)} ▼ ${fmt.bps(r.wan_downstream_bps)}` : "not available"),
    kv("Connected clients", r.connected_clients ?? "not available"),
    kv("Lease valid for", r.lease_time_s != null ? fmt.duration(r.lease_time_s) : "not available"),
  );

  const local = el("dl", { class: "kv" },
    kv("Local DHCP server", sys.dhcp_server),
    kv("Lease obtained", sys.lease_obtained),
    kv("Lease expires", sys.lease_expires),
    kv("Windows Firewall", sys.firewall ? `${sys.firewall.state}${sys.firewall.policy ? " (" + sys.firewall.policy + ")" : ""}` : "not available"),
  );

  const leases = r.dhcp_leases || [];
  const leaseTable = leases.length
    ? el("div", { class: "table-wrap" }, el("table", { class: "data" },
        el("caption", {}, `DHCP leases reported by the authorized router API (${leases.length} clients)`),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col" }, "IP"), el("th", { scope: "col" }, "MAC"), el("th", { scope: "col" }, "Hostname"),
          el("th", { scope: "col" }, "Link"), el("th", { scope: "col" }, "Vendor"),
          el("th", { scope: "col" }, "Lease start"), el("th", { scope: "col" }, "Lease end"))),
        el("tbody", {}, ...leases.map((l) => {
          const tr = el("tr", { tabindex: "0" },
            el("td", { class: "mono" }, l.ip || l.ipv6 || "—"),
            el("td", { class: "mono" }, l.mac || "—"),
            el("td", {}, l.hostname || "—"),
            el("td", {}, l.wireless === true ? "Wi-Fi" : l.wireless === false ? "Ethernet" : el("span", { class: "faint", title: "Router API did not report the link type" }, "Unknown")),
            el("td", {}, l.vendor || "—"),
            el("td", { class: "mono" }, l.lease_start ? fmt.time(l.lease_start) : "—"),
            el("td", { class: "mono" }, l.lease_end ? fmt.time(l.lease_end) : "—"),
          );
          return tr;
        })),
      ))
    : emptyState("No DHCP lease table available. This needs the optional router API (Settings → Router) — LAN Watcher never guesses credentials.", "⇄");

  const integBox = el("div", { class: "panel" },
    el("h2", {}, "Router integration"),
    el("dl", { class: "kv" },
      kv("UPnP IGD", integ.upnp_enabled ? "enabled (router-published API, no credentials)" : "disabled"),
      kv("Admin API", integ.api_enabled ? `${integ.auth_type} @ ${integ.base_url || "?"}` : "not configured"),
    ),
    el("p", { class: "faint", style: "margin:8px 0 0" },
      "Sources used: ", r.source || "local interface data",
      r.api_error ? el("span", { class: "pill danger", title: r.api_error }, " API error") : null),
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "btn", onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try {
          const res = await api.post("/api/settings/test-router", {});
          if (res.ok) toast("Router integration OK — data received", "ok");
          else modalError(res);
        } catch (err) { modalError(err); }
        ev.currentTarget.disabled = false;
      } }, "Test router integration"),
      el("a", { class: "btn ghost", href: "#/settings" }, "Configure…"),
    ),
  );

  mount(container,
    el("div", { class: "grid cols-2" },
      el("div", { class: "panel" }, el("h2", {}, "Router / LAN"), facts),
      el("div", { class: "panel" }, el("h2", {}, "WAN & clients"), wan),
    ),
    el("div", { class: "grid cols-2", style: "margin-top:14px" },
      el("div", { class: "panel" }, el("h2", {}, "Local connection (this PC)"), local),
      integBox,
    ),
    el("div", { class: "panel", style: "margin-top:14px" }, el("h2", {}, "DHCP leases"), leaseTable),
  );

  const unsub = onEvent("router", async () => {
    try {
      const fresh = await api.get("/api/router");
      Object.assign(data, fresh);
      render; // full re-render would drop test button state; leases suffice
    } catch { /* ignore */ }
  });
  return () => unsub();
}

function kv(k, v) {
  return el("div", { style: "display:contents" }, el("dt", {}, k), el("dd", {}, v == null || v === "" ? el("span", { class: "faint", style: "font-family:var(--sans)" }, "not available") : v));
}

function modalError(err) {
  import("../ui.js").then(({ modal }) => {
    modal("Router integration", el("div", { class: "error-box" },
      el("div", { class: "what" }, err.what || "No router data"),
      el("div", { class: "why" }, err.why || ""),
      el("div", { class: "fix" }, err.fix || ""),
    ));
  });
}
