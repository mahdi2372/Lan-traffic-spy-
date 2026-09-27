# Architecture

```
┌─────────────────────────────── Windows desktop ──────────────────────────────┐
│  pywebview window (WebView2)  ──or──  browser tab (local URL + token)        │
│                    web UI: vanilla JS, hash router, SSE                      │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     │  HTTP 127.0.0.1 + token + SSE
┌────────────────────────────────────┴─────────────────────────────────────────┐
│  lanwatcher.server   HTTP server (stdlib) · API dispatcher · static + SPA     │
│  lanwatcher.app      Runtime composition root · _ApiFacade · background loops │
├──────────────────────────────────────────────────────────────────────────────┤
│  services   correlation · discovery · alerts · traffic · history · reports    │
│             metrics · ping · estats (extended Windows statistics)             │
│  collectors WindowsNetwork · ARP · Neighbor · DHCP · Router · WiFi ·          │
│             Ethernet · Flow · Health  (+ SimulatedNetworkProvider for demo)   │
│  parsing    ipconfig · netsh · arp · netstat · wmic · powershell (pure fns)   │
├──────────────────────────────────────────────────────────────────────────────┤
│  db (SQLite, WAL) · events (EventBus) · config · models · secure (DPAPI)      │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Data flow

**Collector → Normalizer → Correlator → Database → EventBus → UI**

1. `DiscoveryService.scan_once()` runs every registered collector with
   `Config + CollectorContext`; each is wrapped in try/except so one failing
   source can never break the scan. Failures are recorded per collector and
   surfaced in Settings → Diagnostics and in error messages ("What happened →
   Why → What to do").
2. Collector output (Observations / NeighborEntry / FlowRecord / …) is
   normalized by `services.correlation`: merged on MAC + IP + hostname + vendor
   + interface + timestamps with **provenance** (`Field → Value → Source →
   Timestamp → Confidence`) written to `device_interfaces` (kind=field history)
   and a cached JSON view for the UI. Conflicting values are retained and
   listed — nothing is silently overwritten.
3. `AlertService` reacts to device.updated events (new device, IP change,
   online/offline, observation conflict) per settings; triage is manual only.
4. Everything is committed in batches to SQLite (`Database.batch()`), and
   events are published on the `EventBus` (`device.*`, `alert.*`, `scan.*`,
   `stats`, `stats.bandwidth`, `health`, `flows`, `collector.*`).
5. The HTTP layer pushes EventBus topics to the UI over SSE; the UI updates
   event-driven with no polling loops.

## Performance & safety

- Async discovery in a background worker; bandwidth loop (psutil) every 5 s;
  retention purge loop with `VACUUM` option; all workers daemonized and joined
  with timeouts on shutdown (graceful cancellation).
- Rate-limited probing: `ping_stats()` bounded (default 3 probes), scan
  interval clamped ≥ 5 s, per-collector timeouts, exponential backoff after
  consecutive failures, optional pause. The app never floods the network.
- DB batching (`executemany`, WAL, indexes on IP/MAC/hostname/timestamps/
  device IDs), connection per thread.
- Metrics: CPU/RSS/threads tracked (`/api/system`), scan durations recorded.
  Recent bandwidth samples capped (ring buffer).

## Error contract

Every API failure returns:

```json
{"error": {"what": "...", "why": "...", "fix": "...", "status": 400}}
```

and the UI renders it as *What happened / Why it may happen / What you can do*.

## Simulated provider

`SimulatedNetworkProvider` (seeded, deterministic) replaces live collectors for
`--demo` and tests: 12+ scripted devices including an iPhone IP change at
t=900 s and a brand-new device at t=1200 s, plus bandwidth/flow/health/router/
Wi-Fi/ethernet data. All tests run against it without touching any real network.

## Desktop shell & packaging

- `lanwatcher/desktop/window.py` — pywebview window pointed at the local URL
  (falls back to browser/server mode when GUI/WebView2 is unavailable).
- `installer/lanwatcher.spec` (PyInstaller one-file, windowed) and
  `installer/LanWatcher.iss` (Inno Setup) build `LANWatcher-Setup-1.0.0.exe`.
  CI (`.github/workflows/ci.yml`) runs both suites on Linux + Windows and
  publishes the exe artifacts.

## Storage schema (SQLite, v1)

`devices`, `device_interfaces`, `device_sessions`, `network_events`,
`traffic_flows`, `alerts`, `router_information`, `network_interfaces`,
`settings`, `tags`, `collector_runs`, `bandwidth_samples` — see
`lanwatcher/db.py` (`SCHEMA_VERSION`) and `docs/USER-GUIDE.md` for retention.
