/* DOM tests (jsdom): helper rendering, state boxes, modal focus/escape,
   and page-level UI state transitions with a mocked API. */
import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <div id="toasts"></div><div id="modal-root"></div><div id="content"></div>
</body></html>`, { url: "http://127.0.0.1:8000/" });

global.window = dom.window;
global.document = dom.window.document;
global.Node = dom.window.Node;
global.Event = dom.window.Event;
global.CustomEvent = dom.window.CustomEvent;
global.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
global.confirm = () => true;

const ui = await import("../../lanwatcher/web/js/ui.js");
const { DeviceTableStore, PageStore, triageTransition } = await import("../../lanwatcher/web/js/state.js");

test("el builds accessible DOM", () => {
  const node = ui.el("button", { class: "btn", "aria-label": "Scan", onclick: () => {} }, "Scan now");
  assert.equal(node.tagName, "BUTTON");
  assert.equal(node.className, "btn");
  assert.equal(node.getAttribute("aria-label"), "Scan");
  assert.equal(node.textContent, "Scan now");
});

test("loading / empty / error states render expected structure", () => {
  const loading = ui.loadingState(2);
  assert.equal(loading.getAttribute("aria-busy"), "true");
  assert.equal(loading.querySelectorAll(".skeleton").length, 2);

  const empty = ui.emptyState("Nothing here");
  assert.match(empty.textContent, /Nothing here/);

  const err = ui.errorState({ what: "Router unavailable", why: "The router API timed out", fix: "Check credentials in Settings" });
  assert.match(err.textContent, /Router unavailable/);
  assert.match(err.textContent, /timed out/);
  assert.match(err.textContent, /Check credentials/);
  assert.equal(err.getAttribute("role"), "alert");
});

test("formatters", () => {
  assert.equal(ui.fmt.bytes(null), "—");
  assert.match(ui.fmt.bytes(5 * 1024 * 1024), /MB/);
  assert.match(ui.fmt.bps(94_000_000), /Mbps/);
  assert.equal(ui.fmt.type("wifi"), "Wi-Fi");
  assert.equal(ui.fmt.type("other"), "Unknown");
  assert.equal(ui.fmt.deviceName({ friendly_name: "Pi" }), "Pi");
  assert.equal(ui.fmt.deviceName({ hostname: "x" }), "x");
  assert.equal(ui.fmt.deviceName({ ipv4: "1.2.3.4" }), "1.2.3.4");
});

test("toast renders and disappears", async () => {
  ui.toast("hello", "ok", 30);
  assert.match(document.getElementById("toasts").textContent, /hello/);
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(document.getElementById("toasts").children.length, 0);
});

test("modal opens, traps content, closes on Escape", () => {
  const body = ui.el("div", {}, "profile body");
  const close = ui.modal("Device profile", body);
  const root = document.getElementById("modal-root");
  assert.match(root.textContent, /profile body/);
  assert.equal(root.querySelector('[role="dialog"]').getAttribute("aria-modal"), "true");
  document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(root.children.length, 0);
  void close;
});

/* ---------------- page-level UI state transitions ---------------- */

function fakeApi(handlers) {
  return {
    get: async (p) => (handlers.get ? handlers.get(p) : {}),
    post: async (p, b) => (handlers.post ? handlers.post(p, b) : {}),
    patch: async (p, b) => (handlers.patch ? handlers.patch(p, b) : {}),
    del: async (p) => (handlers.del ? handlers.del(p) : {}),
    download: async () => "report.html",
  };
}

test("devices page: loading -> ready -> filtered-empty transition", async () => {
  const devices = await import("../../lanwatcher/web/js/pages/devices.js");
  const container = document.createElement("div");
  document.body.append(container);
  let resolveData;
  const pending = new Promise((r) => { resolveData = r; });
  const api = fakeApi({ get: () => pending });
  const done = devices.render(container, { api, params: {}, onEvent: () => () => {} });

  // loading state first
  assert.match(container.textContent, /Loading/);

  resolveData({
    devices: [
      { device_id: "a", ipv4: "192.168.1.5", connection_type: "wifi", online: 1, last_seen: "2026-09-25T10:00:00Z" },
      { device_id: "b", ipv4: "10.0.0.5", connection_type: "ethernet", online: 0, last_seen: "2026-09-24T10:00:00Z" },
    ],
  });
  await done;
  assert.match(container.textContent, /192\.168\.1\.5/);
  assert.match(container.textContent, /2 of 2 devices/);

  // filter to a bucket with no rows -> empty state
  const typeSel = container.querySelector('select[aria-label="Filter by connection type"]');
  typeSel.value = "unknown";
  typeSel.dispatchEvent(new dom.window.Event("change"));
  assert.match(container.textContent, /No devices match the current filters/);
  container.remove();
});

test("devices page: api error renders what/why/fix", async () => {
  const devices = await import("../../lanwatcher/web/js/pages/devices.js");
  const container = document.createElement("div");
  document.body.append(container);
  const api = fakeApi({
    get: () => Promise.reject({ what: "Database failure", why: "The SQLite file is locked", fix: "Restart LAN Watcher" }),
  });
  await devices.render(container, { api, params: {}, onEvent: () => () => {} });
  assert.match(container.textContent, /Database failure/);
  assert.match(container.textContent, /SQLite file is locked/);
  assert.match(container.textContent, /Restart LAN Watcher/);
  container.remove();
});

test("alerts page: triage transition updates state and resolves", async () => {
  const alerts = await import("../../lanwatcher/web/js/pages/alerts.js");
  const container = document.createElement("div");
  document.body.append(container);
  const calls = [];
  let alertState = "unknown";
  const api = fakeApi({
    get: () => Promise.resolve({
      alerts: [{
        id: 7, ts: "2026-09-25T10:00:00Z", kind: "new_device", severity: "warning",
        message: "New device appeared: 192.168.1.50", state: alertState, device_id: "dev_x",
        details: { ip: "192.168.1.50", mac: "aa:bb:cc:dd:ee:ff", vendor: "Acme" },
      }],
    }),
    post: (p, b) => { calls.push([p, b]); alertState = b.state; return Promise.resolve({}); },
  });
  await alerts.render(container, { api, onEvent: () => () => {} });

  // initial render shows the alert and its evidence
  assert.match(container.textContent, /New device appeared/);
  assert.match(container.textContent, /aa:bb:cc:dd:ee:ff/);

  // quick-action: Trusted
  const trustedBtn = [...container.querySelectorAll("button")].find((b) => b.textContent.includes("Trusted"));
  trustedBtn.click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(calls.at(-1), ["/api/alerts/7/triage", { state: "trusted" }]);
  assert.equal(triageTransition("unknown", "trusted").resolved, true);

  // invalid state via select must not call the API
  const sel = container.querySelector('select[aria-label="Triage alert 7"]');
  const before = calls.length;
  const opt = document.createElement("option");
  opt.value = "rootkit";
  sel.append(opt);
  sel.value = "rootkit";
  sel.dispatchEvent(new dom.window.Event("change"));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(calls.length, before);
  container.remove();
});

test("history page: events render as Device -> Event -> Timestamp -> Details", async () => {
  const history = await import("../../lanwatcher/web/js/pages/history.js");
  const container = document.createElement("div");
  document.body.append(container);
  const api = fakeApi({
    get: (p) => {
      if (p.startsWith("/api/devices")) return Promise.resolve({ devices: [{ device_id: "dev_a", hostname: "pc1", ipv4: "1.2.3.4" }] });
      return Promise.resolve({
        events: [
          { ts: "2026-09-25T09:00:00Z", event_type: "ip_change", device_id: "dev_a", label: "IP address changed: 1.2.3.4 -> 1.2.3.5", details: {} },
          { ts: "2026-09-25T08:00:00Z", event_type: "new_device", device_id: "dev_a", label: "New device discovered", details: {} },
        ],
      });
    },
  });
  await history.render(container, { api, params: {}, onEvent: () => () => {} });
  assert.match(container.textContent, /2 event/);
  assert.match(container.textContent, /IP address changed: 1\.2\.3\.4 -> 1\.2\.3\.5/);
  assert.match(container.textContent, /ip_change/);

  // switch to a range with no events -> empty state
  const presetSel = container.querySelector('select[aria-label="Time range"]');
  api.get = (p) => p.startsWith("/api/devices")
    ? Promise.resolve({ devices: [] })
    : Promise.resolve({ events: [] });
  presetSel.value = "today";
  presetSel.dispatchEvent(new dom.window.Event("change"));
  await new Promise((r) => setTimeout(r, 20));
  assert.match(container.textContent, /No events in this range/);
  container.remove();
});
