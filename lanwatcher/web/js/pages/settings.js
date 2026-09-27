/* Settings: scanning, privacy, retention, alerts, router integration, traffic,
   diagnostics (collector status, resource usage), data tools. */

import { el, mount, fmt, loadingState, errorState, toast, modal } from "../ui.js";

export async function render(container, ctx) {
  const { api } = ctx;
  mount(container, loadingState(6));

  let data;
  try {
    data = await api.get("/api/settings");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }
  const s = data.settings;

  const changed = new Map();
  const mark = (key, value) => changed.set(key, value);

  function num(key, min, max) {
    return el("input", {
      type: "number", value: s_general(key), min, max,
      onchange: (ev) => mark(key, +ev.target.value),
    });
  }
  function s_general(key) { return lookup(s, key); }
  function lookup(obj, dotted) {
    return dotted.split(".").reduce((o, k) => (o || {})[k], obj);
  }
  function check(key, label, tip) {
    const input = el("input", { type: "checkbox", checked: !!lookup(s, key), onchange: (ev) => mark(key, ev.target.checked) });
    return el("label", { class: "check", title: tip || "" }, input, label);
  }
  function text(key, placeholder = "") {
    return el("input", { type: "text", value: lookup(s, key) || "", placeholder, style: "width:320px", onchange: (ev) => mark(key, ev.target.value) });
  }

  const scanPanel = el("div", { class: "panel" },
    el("h2", {}, "Discovery & scanning"),
    el("div", { class: "row" },
      el("label", {}, "Scan interval (s) ", num("general.scan_interval_s", 5, 3600)),
      el("label", {}, "Offline threshold (s) ", num("general.offline_threshold_s", 15, 86400)),
    ),
    el("div", { class: "row", style: "margin-top:8px" },
      el("label", {}, "Ping concurrency ", num("general.ping_concurrency", 1, 128)),
    ),
    el("div", { class: "row", style: "margin-top:8px" },
      check("general.ping_enabled", "ICMP reachability probes (rate-limited)"),
      check("general.ipv6_enabled", "IPv6 neighbour discovery"),
      check("discovery.arp", "ARP table"),
      check("discovery.neighbors", "Neighbour discovery"),
      check("discovery.resolve_hostnames", "Resolve hostnames"),
    ),
  );

  const privacyPanel = el("div", { class: "panel" },
    el("h2", {}, "Privacy & security"),
    el("div", { class: "row" },
      check("privacy.local_only_mode", "Local-only mode", "Disables every external probe. Discovery/history/reports remain fully functional."),
    ),
    el("div", { class: "row", style: "margin-top:6px" },
      check("privacy.internet_probe_enabled", "Internet reachability probe (HTTP GET to the configured URL — visible, toggleable)"),
    ),
    el("div", { class: "row", style: "margin-top:6px" },
      el("label", {}, "Probe URL ", text("privacy.internet_probe_url", "http://www.msftconnecttest.com/connecttest.txt")),
    ),
    el("div", { class: "row", style: "margin-top:6px" },
      check("privacy.encrypt_notes", "Encrypt stored notes & router credentials at rest"),
    ),
    el("p", { class: "faint", style: "margin:8px 0 0;font-size:11.5px" },
      `Telemetry: permanently disabled. No data leaves this machine. Secure storage: ${data.secure_available ? "available" : "unavailable on this platform"}.`,
      data.elevated ? " Running elevated." : ""),
  );

  const retentionPanel = el("div", { class: "panel" },
    el("h2", {}, "Data retention"),
    el("div", { class: "row" },
      el("label", {}, "Flows (days) ", num("retention.flows_days", 1, 3650)),
      el("label", {}, "Events (days) ", num("retention.events_days", 1, 3650)),
      el("label", {}, "Sessions (days) ", num("retention.sessions_days", 1, 3650)),
      el("label", {}, "Alerts (days) ", num("retention.alerts_days", 1, 3650)),
    ),
    el("div", { class: "row", style: "margin-top:6px" }, check("retention.vacuum_on_purge", "Compact database after purge")),
  );

  const alertsPanel = el("div", { class: "panel" },
    el("h2", {}, "Alerts"),
    el("div", { class: "row" },
      check("alerts.new_device", "New device alerts"),
      check("alerts.ip_change", "IP change alerts"),
      check("alerts.device_online", "Device online alerts"),
      check("alerts.device_offline", "Device offline alerts"),
    ),
    el("p", { class: "faint", style: "margin:8px 0 0;font-size:11.5px" },
      "Alerts are advisory. LAN Watcher never attacks, blocks or disconnects any device."),
  );

  const routerPanel = el("div", { class: "panel" },
    el("h2", {}, "Router integration (optional, admin-authorized)"),
    el("div", { class: "row" }, check("router.upnp_enabled", "UPnP IGD (router-published API — no credentials)")),
    el("div", { class: "row", style: "margin-top:6px" }, check("router.api_enabled", "Enable admin API integration")),
    el("div", { class: "row", style: "margin-top:6px" }, el("label", {}, "API base URL ", text("router.base_url", "https://router.lan"))),
    el("div", { class: "row", style: "margin-top:6px" },
      el("label", {}, "Auth type ",
        el("select", { onchange: (ev) => mark("router.auth_type", ev.target.value) },
          ...["none", "basic", "bearer", "api_key"].map((a) => el("option", { value: a, selected: s.router?.auth_type === a }, a)))),
      el("label", {}, "Username ", text("router.username")),
    ),
    el("div", { class: "row", style: "margin-top:6px" },
      el("label", {}, "Password ", el("input", { type: "password", placeholder: "(stored encrypted — leave blank to keep)", onchange: (ev) => ev.target.value && mark("router.password", ev.target.value) })),
      el("label", {}, "Token / API key ", el("input", { type: "password", placeholder: "(stored encrypted — leave blank to keep)", onchange: (ev) => ev.target.value && mark("router.token", ev.target.value) })),
    ),
    el("div", { class: "row", style: "margin-top:6px" }, check("router.verify_tls", "Verify TLS certificates")),
    el("p", { class: "faint", style: "margin:8px 0 0;font-size:11.5px" },
      "LAN Watcher only talks to an API you explicitly configure with credentials you supply. ",
      "It never attempts logins or credential guessing. See docs/ROUTER-API.md for the JSON contract."),
  );

  const trafficPanel = el("div", { class: "panel" },
    el("h2", {}, "Traffic monitoring"),
    el("div", { class: "row" },
      check("traffic.enabled", "Collect connection metadata (5-tuple)"),
      el("label", {}, "Sample interval (s) ", num("traffic.interval_s", 1, 300)),
    ),
    el("div", { class: "row", style: "margin-top:6px" },
      check("traffic.per_flow_bytes", "Per-flow byte counters (requires administrator — Windows IP Helper stats; read-only)"),
    ),
    el("p", { class: "faint", style: "margin:8px 0 0;font-size:11.5px" },
      "Metadata only: addresses, ports, protocol, time, duration. Never payloads, credentials or contents."),
  );

  const diagPanel = el("div", { class: "panel" }, el("h2", {}, "Diagnostics"), loadingState(3));
  const toolsPanel = el("div", { class: "panel" },
    el("h2", {}, "Data tools"),
    el("div", { class: "row" },
      el("button", { class: "btn", onclick: async () => { try { await api.download("json", "30d"); toast("History & data exported (JSON)", "ok"); } catch (err) { toast(err.what || err.message || "Export failed", "error"); } } }, "Export history & data (JSON)"),
      el("button", { class: "btn", onclick: () => tool("clear-logs", "Clear event & flow logs?") }, "Clear logs"),
      el("button", { class: "btn", onclick: () => tool("purge-retention", "Run retention purge now?") }, "Purge expired data"),
      el("button", { class: "btn danger", onclick: () => tool("delete-history", "Delete ALL history (events, sessions, flows, alerts)? Devices keep their names/notes.", { keep_devices: true }) }, "Delete history"),
      el("button", { class: "btn danger", onclick: () => tool("delete-history", "Delete ALL data including the device inventory?", { keep_devices: false }) }, "Delete everything"),
      el("button", { class: "btn ghost", onclick: () => tool("reset-settings", "Reset all settings to defaults?") }, "Reset settings"),
    ),
  );

  const saveRow = el("div", { class: "row", style: "margin-top:14px" },
    el("button", { class: "btn primary", onclick: save }, "Save settings"),
    el("span", { class: "faint", id: "save-status" }, "no changes"),
  );

  mount(container,
    el("div", { class: "grid cols-2" }, scanPanel, privacyPanel, retentionPanel, alertsPanel, trafficPanel, routerPanel),
    saveRow, diagPanel, toolsPanel);

  async function save() {
    if (!changed.size) return;
    try {
      const values = Object.fromEntries(changed);
      await api.patch("/api/settings", { values });
      changed.clear();
      document.getElementById("save-status").textContent = "saved ✓";
      toast("Settings saved", "ok");
      if (values["general.scan_interval_s"] || values["privacy.local_only_mode"] !== undefined) {
        toast("Some changes apply from the next scan cycle", "info");
      }
    } catch (err) {
      toast(`${err.what || "Save failed"} — ${err.fix || ""}`, "danger", 5500);
    }
  }

  async function tool(action, confirmMsg, body = {}) {
    if (!confirm(confirmMsg)) return;
    try {
      const res = await api.post(`/api/tools/${action}`, body);
      toast(`Done: ${JSON.stringify(res)}`, "ok", 3000);
    } catch (err) {
      toast(`${err.what || "Tool failed"}`, "danger", 5000);
    }
  }

  // diagnostics
  try {
    const sys = await api.get("/api/system");
    const m = sys.metrics || {};
    mount(diagPanel, el("h2", {}, "Diagnostics"),
      el("dl", { class: "kv" },
        kv("Version", `${sys.version}${sys.demo_mode ? " (demo mode)" : ""}`),
        kv("Platform", `${sys.platform?.system} ${sys.platform?.release} ${sys.platform?.machine}`),
        kv("Python", sys.platform?.python),
        kv("Elevated", sys.platform?.elevated ? "yes" : "no"),
        kv("CPU usage", m.cpu_percent != null ? `${m.cpu_percent}%` : "—"),
        kv("Memory (RSS)", m.rss_mb != null ? `${m.rss_mb} MB` : "—"),
        kv("Threads", m.threads ?? "—"),
        kv("Uptime", m.uptime_s != null ? fmt.duration(m.uptime_s) : "—"),
        kv("Database size", `${sys.db_size_mb} MB`),
      ),
      el("h2", { style: "margin-top:10px" }, "Collectors"),
      el("div", { class: "table-wrap" }, el("table", { class: "data" },
        el("thead", {}, el("tr", {},
          el("th", { scope: "col" }, "Collector"), el("th", { scope: "col" }, "Status"),
          el("th", { scope: "col" }, "Runs"), el("th", { scope: "col" }, "Errors"),
          el("th", { scope: "col" }, "Last run"), el("th", { scope: "col" }, "Detail"))),
        el("tbody", {}, ...(sys.collectors || []).map((c) => el("tr", { tabindex: "0" },
          el("td", {}, c.name, el("div", { class: "faint", style: "font-size:10.5px" }, c.detail || "")),
          el("td", {}, el("span", { class: `pill ${c.ok ? "ok" : "danger"}`, title: c.last_error || c.last_unavailable || "" },
            c.ok ? "ok" : (c.last_unavailable ? "unavailable" : "error"))),
          el("td", { class: "num" }, c.runs),
          el("td", { class: "num" }, c.errors),
          el("td", { class: "mono" }, c.last_run ? fmt.ago(c.last_run) : "—"),
          el("td", { class: "faint", style: "font-size:11px" }, c.last_error || c.last_unavailable || ""),
        ))),
      )),
    );
  } catch (err) {
    mount(diagPanel, el("h2", {}, "Diagnostics"), errorState(err));
  }
}

function kv(k, v) {
  return el("div", { style: "display:contents" }, el("dt", {}, k), el("dd", {}, v == null || v === "" ? "—" : v));
}
