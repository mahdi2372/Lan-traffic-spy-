/* Wi-Fi page: adapter association + visible networks. Passwords are never
   requested, displayed or stored — by design. */

import { el, mount, fmt, loadingState, errorState, emptyState, typePill } from "../ui.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(3));

  let data;
  try {
    data = await api.get("/api/wifi");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const links = data.links || [];
  const visible = data.visible_networks || [];

  const linkCards = el("div", { class: "grid cols-2" });
  if (!links.length) {
    linkCards.append(emptyState("No Wi-Fi association data. This requires Windows (netsh wlan) and an active wireless adapter.", "⌃"));
  }
  for (const w of links) {
    linkCards.append(el("div", { class: "panel" },
      el("h2", {}, `Wi-Fi interface ${w.interface || "?"}`),
      el("dl", { class: "kv" },
        kv("SSID", w.ssid),
        kv("BSSID (AP)", fmt.mac(w.bssid)),
        kv("State", w.state),
        kv("Signal strength", w.signal_pct != null ? `${w.signal_pct}%` : null),
        kv("Channel", w.channel),
        kv("Radio type", w.radio_type),
        kv("Frequency / band", w.frequency_mhz ? `${w.frequency_mhz} MHz · ${w.band || "?"}` : w.band),
        kv("Connection speed", w.receive_rate_mbps ? `▼ ${w.receive_rate_mbps} Mbps  ▲ ${w.transmit_rate_mbps ?? "?"} Mbps` : null),
        kv("Security mode", w.security),
      ),
      el("p", { class: "faint", style: "margin:8px 0 0;font-size:11.5px" },
        "Security mode is displayed as reported by Windows. Wi-Fi passwords/keys are never requested or stored."),
    ));
  }

  const netsBody = visible.length
    ? el("div", { class: "table-wrap" }, el("table", { class: "data" },
        el("caption", {}, `Visible networks from the last scan (${visible.length} SSIDs)`),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col" }, "SSID"), el("th", { scope: "col" }, "BSSID"),
          el("th", { scope: "col" }, "Signal"), el("th", { scope: "col" }, "Channel"),
          el("th", { scope: "col" }, "Radio"), el("th", { scope: "col" }, "Authentication"), el("th", { scope: "col" }, "Encryption"))),
        el("tbody", {}, ...visible.flatMap((n) => (n.bssids || []).map((b) => el("tr", { tabindex: "0" },
          el("td", {}, n.ssid || el("span", { class: "faint" }, "(hidden)")),
          el("td", { class: "mono" }, fmt.mac(b.bssid)),
          el("td", { class: "num" }, b.signal_pct != null ? `${b.signal_pct}%` : "—"),
          el("td", { class: "num" }, b.channel ?? n.channel ?? "—"),
          el("td", {}, b.radio_type || n.radio_type || "—"),
          el("td", {}, n.authentication || b.authentication || "—"),
          el("td", {}, n.encryption || "—"),
        ))))),
      ))
    : emptyState(visible.length === 0 ? "No visible networks cached — run a scan (⟳) on Windows to collect nearby AP metadata" : "Empty", "⌃");

  mount(container,
    el("div", { class: "panel", style: "margin-bottom:12px" },
      el("h2", {}, "Privacy"),
      el("p", { class: "dim", style: "margin:0" },
        "LAN Watcher reads only association metadata (SSID/BSSID/signal/channel/radio/rates/security mode). ",
        "It does not attempt to obtain Wi-Fi passwords, keys or traffic contents.")),
    linkCards,
    el("div", { class: "panel", style: "margin-top:14px" }, el("h2", {}, "Visible networks"), netsBody),
  );

  const unsub = onEvent("wifi", () => {});
  return () => unsub();
}

function kv(k, v) {
  return el("div", { style: "display:contents" },
    el("dt", {}, k),
    el("dd", {}, v == null || v === "" ? el("span", { class: "faint", style: "font-family:var(--sans)" }, "not available") : v));
}
