/* Network Map: honest topology visualization. */

import { el, mount, loadingState, errorState, emptyState, fmt } from "../ui.js";
import { renderTopology } from "../topology.js";
import { openDeviceDetail } from "./device_detail.js";

export async function render(container, ctx) {
  const { api, onEvent } = ctx;
  mount(container, loadingState(3));

  let topo;
  try {
    topo = await api.get("/api/topology");
  } catch (err) {
    mount(container, errorState(err));
    return;
  }

  const svg = el("svg", { role: "img", "aria-label": "Network topology" });
  const legend = el("div", { class: "panel", style: "margin-top:12px" },
    el("h2", {}, "How to read this map"),
    el("p", { class: "dim", style: "margin:0" },
      "Internet connects to your router/gateway. Access points (APs) are shown only when they were observed (Wi-Fi BSSIDs). ",
      "Clients attach to the wired, wireless or unverified segment according to the evidence we actually have — ",
      "LAN Watcher never invents topology relationships it cannot establish."),
  );
  mount(container, el("div", { class: "panel topo-wrap" }, svg), legend);

  function draw() {
    renderTopology(svg, topo, {
      onNodeClick: (node) => {
        if (node.device_id) openDeviceDetail(node.device_id, api);
      },
    });
  }
  draw();

  const unsubs = [
    onEvent("device.new", scheduleRefresh),
    onEvent("device.updated", scheduleRefresh),
    onEvent("device.online", scheduleRefresh),
    onEvent("device.offline", scheduleRefresh),
    onEvent("router", scheduleRefresh),
  ];
  let timer = null;
  function scheduleRefresh() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        topo = await api.get("/api/topology");
        draw();
      } catch { /* keep last graph */ }
    }, 800);
  }
  window.addEventListener("resize", draw);

  return () => {
    unsubs.forEach((u) => u());
    clearTimeout(timer);
    window.removeEventListener("resize", draw);
  };
}
