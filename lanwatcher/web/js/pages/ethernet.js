/* Ethernet page: wired adapter link state, speed, addressing, DNS, uptime. */

import { el, mount, fmt, loadingState, errorState, emptyState } from "../ui.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(3));

  let data;
  try {
    data = await api.get("/api/interfaces");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const ifaces = (data.interfaces || []).filter((i) => i.kind === "ethernet" || i.kind === "unknown");
  const all = data.interfaces || [];
  const adapters = data.adapters || [];

  const cards = el("div", { class: "grid cols-2" });
  const list = ifaces.length ? ifaces : all;
  if (!list.length) {
    cards.append(emptyState("No interface data yet", "⧉"));
  }
  for (const i of list) {
    const adapter = adapters.find((a) => a.name === i.name) || {};
    cards.append(el("div", { class: "panel" },
      el("h2", {}, `${i.name} `, el("span", { class: `pill ${i.kind === "ethernet" ? "violet" : "neutral"}` }, i.kind || "unknown"),
        el("span", { class: `pill ${i.is_up ? "ok" : "danger"}`, style: "margin-left:6px" }, i.is_up ? "up" : "down")),
      el("dl", { class: "kv" },
        kv("Adapter name", i.name),
        kv("Description", i.description || adapter.description),
        kv("MAC", fmt.mac(i.mac)),
        kv("Link state", i.is_up ? "connected" : "disconnected"),
        kv("Link speed", i.link_speed_bps ? fmt.bps(i.link_speed_bps) : adapter.speed_bps ? fmt.bps(adapter.speed_bps) : null),
        kv("MTU", i.mtu),
        kv("IPv4", i.ipv4 ? `${i.ipv4}${i.ipv4_prefix != null ? "/" + i.ipv4_prefix : ""}` : null),
        kv("IPv6", i.ipv6),
        kv("Gateway", i.gateway),
        kv("DNS", i.dns),
        kv("Network profile", i.profile),
      ),
    ));
  }

  const windowsBits = el("div", { class: "panel", style: "margin-top:14px" },
    el("h2", {}, "Windows integration status"),
    el("p", { class: "dim", style: "margin:0" },
      "Adapter details come from Get-NetAdapter (link speed, state) and ipconfig/route (addressing, DNS, gateway). ",
      "Connection uptime is tracked per device session in History. No administrator rights are required for these read-only queries."),
  );

  mount(container, cards, windowsBits);
  const unsub = onEvent("interfaces", () => {});
  return () => unsub();
}

function kv(k, v) {
  return el("div", { style: "display:contents" },
    el("dt", {}, k),
    el("dd", {}, v == null || v === "" ? el("span", { class: "faint", style: "font-family:var(--sans)" }, "not available") : v));
}
