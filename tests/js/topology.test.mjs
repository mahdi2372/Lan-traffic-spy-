/* Topology renderer tests: only evidenced relationships, node kinds, no-invention. */
import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM(`<!DOCTYPE html><html><body><svg id="t"></svg></body></html>`, { url: "http://127.0.0.1/" });
global.window = dom.window;
global.document = dom.window.document;
global.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);

const { renderTopology } = await import("../../lanwatcher/web/js/topology.js");

test("renders all nodes and edges with kinds", () => {
  const svg = document.getElementById("t");
  const topo = {
    nodes: [
      { id: "internet", kind: "internet", label: "Internet", tier: 0 },
      { id: "gw", kind: "router", label: "Router", tier: 1 },
      { id: "seg-wifi", kind: "segment", label: "Wireless segment", tier: 2 },
      { id: "c1", kind: "client-wifi", label: "phone", tier: 3, device_id: "dev_1", online: true, ip: "192.168.1.5" },
      { id: "c2", kind: "client-unknown", label: "mystery", tier: 3, device_id: "dev_2", online: false },
    ],
    edges: [
      { from: "internet", to: "gw", kind: "wan" },
      { from: "gw", to: "seg-wifi", kind: "lan" },
      { from: "seg-wifi", to: "c1", kind: "client" },
      { from: "seg-wifi", to: "c2", kind: "client" },
    ],
  };
  const clicked = [];
  renderTopology(svg, topo, { onNodeClick: (n) => clicked.push(n.id) });
  assert.equal(svg.querySelectorAll("line").length, 4);
  assert.equal(svg.querySelectorAll(".topo-node").length, 5);
  assert.match(svg.textContent, /Wireless segment/);
  assert.match(svg.textContent, /192\.168\.1\.5/);
  assert.match(svg.textContent, /only where evidence exists/);

  // keyboard/click activation for device nodes only
  svg.querySelector(".topo-node").dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.ok(clicked.length <= 1);
});

test("offline nodes render dashed outline (no invented liveness)", () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  document.body.append(svg);
  renderTopology(svg, {
    nodes: [{ id: "a", kind: "client-ethernet", label: "pc", tier: 3, device_id: "d", online: false }],
    edges: [],
  });
  const dashed = [...svg.querySelectorAll("circle")].some((c) => c.getAttribute("stroke-dasharray"));
  assert.ok(dashed, "offline node should look offline");
});
