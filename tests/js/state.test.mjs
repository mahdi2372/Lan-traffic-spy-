/* Pure state-store tests: device table filtering/sorting, triage transitions,
   page lifecycle states, search sequencing, series buffer. */
import test from "node:test";
import assert from "node:assert/strict";

import {
  DeviceTableStore,
  PageStore,
  SearchStore,
  SeriesBuffer,
  triageTransition,
  TRIAGE_STATES,
} from "../../lanwatcher/web/js/state.js";

const rows = [
  { device_id: "a", ipv4: "192.168.1.5", mac: "aa:bb:cc:dd:ee:ff", hostname: "laptop", vendor: "Apple", connection_type: "wifi", online: 1, last_seen: "2026-09-25T10:00:00Z", bytes_sent: 1000, last_latency_ms: 5 },
  { device_id: "b", ipv4: "10.0.0.5", mac: "11:22:33:44:55:66", hostname: "printer", vendor: "HP", connection_type: "ethernet", online: 0, last_seen: "2026-09-24T10:00:00Z", pinned: 1, last_latency_ms: null },
  { device_id: "c", ipv4: "192.168.1.9", mac: "77:88:99:aa:bb:cc", connection_type: "unknown", online: 1, last_seen: "2026-09-25T09:00:00Z", category: "iot" },
];

function store() {
  const s = new DeviceTableStore();
  s.setRows(rows.map((r) => ({ ...r }))); // isolate mutable fields (pinned) per test
  return s;
}

test("filter by connection type incl. unknown bucket", () => {
  const s = store();
  s.setType("wifi");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["a"]);
  s.setType("ethernet");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["b"]);
  s.setType("unknown");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["c"]);
});

test("filter by status", () => {
  const s = store();
  s.setStatus("online");
  assert.equal(s.visible().length, 2);
  s.setStatus("offline");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["b"]);
});

test("search matches ip, mac, hostname, vendor, name", () => {
  const s = store();
  s.setSearch("192.168.1.5");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["a"]);
  s.setSearch("11-22-33");
  s.rows[1].mac = "11:22:33:44:55:66";
  s.setSearch("11:22:33");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["b"]);
  s.setSearch("apple");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["a"]);
  s.setSearch("iot");
  assert.deepEqual(s.visible().map((r) => r.device_id), ["c"]);
});

test("sort by bandwidth, latency, last_seen with direction toggle", () => {
  const s = store();
  s.togglePin("b"); // isolate sorting from pin preference
  s.setSort("bandwidth"); // first press on new column -> desc
  assert.equal(s.order, "desc");
  assert.equal(s.visible().map((r) => r.device_id)[0], "a");
  s.setSort("bandwidth"); // same column -> direction flips
  assert.equal(s.order, "asc");
  s.sort = "latency"; s.order = "asc";
  assert.equal(s.visible().map((r) => r.device_id)[0], "a"); // 5 ms first; unknown latency sorts last in asc
  s.sort = "latency"; s.order = "desc";
  assert.equal(s.visible().map((r) => r.device_id).at(-1), "a");
  s.sort = "last_seen"; s.order = "desc";
  assert.deepEqual(s.visible().map((r) => r.device_id), ["a", "c", "b"]);
});

test("pinned devices always float to top", () => {
  const s = store();
  s.sort = "last_seen"; s.order = "desc";
  assert.equal(s.visible().map((r) => r.device_id)[0], "b"); // pinned first despite older last_seen
  s.togglePin("b");
  assert.equal(s.visible().map((r) => r.device_id)[0], "a");
});

test("grouping by type and category", () => {
  const s = store();
  s.setGroup("type");
  const keys = s.grouped().map((g) => g.key).sort();
  assert.deepEqual(keys, ["ethernet", "unknown", "wifi"]);
  s.setGroup("category");
  assert.ok(s.grouped().some((g) => g.key === "iot"));
});

test("selection pruning when rows update", () => {
  const s = store();
  s.toggleSelect("a");
  s.toggleSelect("gone");
  assert.equal(s.selected.size, 2);
  s.setRows(rows.slice(1));
  assert.deepEqual([...s.selected], []);
});

test("triage transitions validate and resolve", () => {
  assert.equal(triageTransition("unknown", "trusted").resolved, true);
  assert.equal(triageTransition("unknown", "ignore").resolved, true);
  assert.equal(triageTransition("unknown", "investigate").resolved, false);
  assert.equal(triageTransition("trusted", "trusted").unchanged, true);
  assert.equal(triageTransition("trusted", "trusted").ok, false);
  assert.equal(triageTransition("unknown", "hack").ok, false);
  for (const st of TRIAGE_STATES) {
    const res = triageTransition("unknown", st);
    if (st === "unknown") assert.equal(res.unchanged, true);
    else { assert.equal(res.ok, true); assert.equal(res.to, st); }
  }
});

test("page store lifecycle: idle -> loading -> ready | error", () => {
  const p = new PageStore();
  assert.equal(p.state, "idle");
  p.start();
  assert.ok(p.isLoading);
  p.succeed({ x: 1 });
  assert.ok(p.isReady);
  assert.deepEqual(p.data, { x: 1 });
  p.start();
  p.fail({ what: "boom" });
  assert.ok(p.isError);
  assert.equal(p.error.what, "boom");
});

test("search store debounces and exposes seq for stale-response rejection", async () => {
  const seen = [];
  const s = new SearchStore((value, seq) => seen.push([value, seq]), 5);
  s.set("192.");
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(seen, [["192.", 1]]);
  s.set("192.168"); // superseded before the debounce fires
  s.set("192.168.1.5", true);
  assert.deepEqual(seen.at(-1), ["192.168.1.5", 3]);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(seen.length, 2); // superseded value never fired
});

test("series buffer keeps bounded history", () => {
  const b = new SeriesBuffer(3);
  for (let i = 0; i < 5; i++) b.push({ total_bps: i });
  assert.equal(b.points.length, 3);
  assert.equal(b.last.total_bps, 4);
  assert.deepEqual(b.values("total_bps"), [2, 3, 4]);
});
