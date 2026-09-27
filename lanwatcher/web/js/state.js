/* Pure UI state stores — no DOM. Unit-tested with node:test. */

/* ---------------- device table store ---------------- */
export class DeviceTableStore {
  constructor() {
    this.rows = [];
    this.search = "";
    this.type = "";       // '' | wifi | ethernet | unknown
    this.status = "";     // '' | online | offline
    this.sort = "last_seen";
    this.order = "desc";
    this.groupBy = "";    // '' | type | category
    this.selected = new Set();
    this.setRows([]);
  }

  setRows(rows) {
    this.rows = Array.isArray(rows) ? rows.slice() : [];
    // prune selections that no longer exist
    const ids = new Set(this.rows.map((r) => r.device_id));
    for (const id of [...this.selected]) if (!ids.has(id)) this.selected.delete(id);
  }

  setSearch(q) { this.search = (q || "").trim().toLowerCase(); }
  setType(t) { this.type = t || ""; }
  setStatus(s) { this.status = s || ""; }
  setGroup(g) { this.groupBy = g || ""; }

  setSort(column) {
    if (this.sort === column) {
      this.order = this.order === "asc" ? "desc" : "asc";
    } else {
      this.sort = column;
      this.order = column === "name" ? "asc" : "desc";
    }
  }

  toggleSelect(id) {
    if (this.selected.has(id)) this.selected.delete(id);
    else this.selected.add(id);
  }

  togglePin(id) {
    const row = this.rows.find((r) => r.device_id === id);
    if (row) row.pinned = row.pinned ? 0 : 1;
  }

  matches(row) {
    if (this.type) {
      const t = row.connection_type || "unknown";
      const norm = ["wifi", "ethernet"].includes(t) ? t : "unknown";
      if (norm !== this.type) return false;
    }
    if (this.status) {
      const online = !!row.online;
      if (this.status === "online" && !online) return false;
      if (this.status === "offline" && online) return false;
    }
    if (this.search) {
      const hay = [row.ipv4, row.ipv6, row.mac, row.mac_norm, row.hostname, row.vendor, row.friendly_name,
        row.category, row.connection_type, row.trust_state, row.device_id, row.notes]
        .filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(this.search)) return false;
    }
    return true;
  }

  sortKey(row) {
    switch (this.sort) {
      case "bandwidth": return (row.bytes_sent || 0) + (row.bytes_recv || 0);
      case "latency": return row.last_latency_ms == null ? 1e12 : row.last_latency_ms;
      case "name": return (row.friendly_name || row.hostname || row.ipv4 || row.mac || "").toLowerCase();
      case "upload": return row.bytes_sent == null ? -1 : row.bytes_sent;
      case "download": return row.bytes_recv == null ? -1 : row.bytes_recv;
      default: return row[this.sort] ?? "";
    }
  }

  visible() {
    const rows = this.rows.filter((r) => this.matches(r));
    const dir = this.order === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      if (!!b.pinned !== !!a.pinned) return b.pinned ? 1 : -1; // pinned first
      const ka = this.sortKey(a), kb = this.sortKey(b);
      if (ka < kb) return -1 * dir;
      if (ka > kb) return 1 * dir;
      return 0;
    });
    return rows;
  }

  grouped() {
    const rows = this.visible();
    if (!this.groupBy) return [{ key: null, rows }];
    const groups = new Map();
    for (const r of rows) {
      let key = "unknown";
      if (this.groupBy === "type") {
        const t = r.connection_type || "unknown";
        key = ["wifi", "ethernet"].includes(t) ? t : "unknown";
      } else if (this.groupBy === "category") {
        key = r.category || "unknown";
      }
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(r);
    }
    return [...groups.entries()].map(([key, rs]) => ({ key, rows: rs }));
  }
}

/* ---------------- alert triage transitions ---------------- */
export const TRIAGE_STATES = ["trusted", "unknown", "ignore", "investigate"];

export function triageTransition(current, next) {
  if (!TRIAGE_STATES.includes(next)) {
    return { ok: false, error: `Invalid triage state '${next}'. Allowed: ${TRIAGE_STATES.join(", ")}.` };
  }
  if (current === next) return { ok: false, error: `Alert is already '${next}'.`, unchanged: true };
  const resolved = next === "trusted" || next === "ignore";
  return { ok: true, from: current, to: next, resolved };
}

/* ---------------- page lifecycle (loading -> ready | error) ---------------- */
export class PageStore {
  constructor() {
    this.state = "idle"; // idle | loading | ready | error
    this.data = null;
    this.error = null;
  }
  start() { this.state = "loading"; this.error = null; return this.state; }
  succeed(data) { this.state = "ready"; this.data = data; this.error = null; return this.state; }
  fail(error) { this.state = "error"; this.error = error; return this.state; }
  get isLoading() { return this.state === "loading"; }
  get isReady() { return this.state === "ready"; }
  get isError() { return this.state === "error"; }
}

/* ---------------- search debounce state ---------------- */
export class SearchStore {
  constructor(fn, ms = 180) {
    this.fn = fn;
    this.ms = ms;
    this.value = "";
    this.seq = 0;
    this._t = null;
  }
  set(value, immediate = false) {
    this.value = value;
    const seq = ++this.seq;
    clearTimeout(this._t);
    const fire = () => this.fn(this.value, seq);
    if (immediate) fire();
    else this._t = setTimeout(fire, this.ms);
  }
  /* late responses with stale seq are ignored by callers via seq arg */
}

/* ---------------- bandwidth ring buffer ---------------- */
export class SeriesBuffer {
  constructor(limit = 300) {
    this.limit = limit;
    this.points = [];
  }
  push(point) {
    this.points.push(point);
    if (this.points.length > this.limit) this.points.splice(0, this.points.length - this.limit);
  }
  values(key) { return this.points.map((p) => p[key] ?? 0); }
  get last() { return this.points[this.points.length - 1] || null; }
}
