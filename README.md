# LAN Watcher

**Privacy-preserving LAN monitoring & inventory for Windows** — see every device
on a network you own or are authorized to administer, watch bandwidth and
health live, get alerted on new devices, and keep a searchable history. 100%
offline: no telemetry, no cloud, no packet contents, ever.

![Dashboard](docs/img/dashboard.svg)

## Features

- **Discovery** with graceful fallback: router/DHCP data (authorized API only),
  ARP table, Windows Neighbor Discovery, ICMP reachability, interface
  enumeration, IPv4 subnet scanning, IPv6 neighbours.
- **Device inventory** — IP/MAC/OUI vendor/hostname/link type (Wi-Fi · Ethernet
  · **Unknown — never guessed**)/interface/latency/first-last seen + your own
  names, notes, tags and pin/favorite.
- **Device details** with connectivity stats (online duration, latency, packet
  loss from safe probes) and **activity metadata only** (bytes, flow counts,
  top destinations/ports, protocols) — payload contents are never captured.
- **Real-time dashboard** — totals, bandwidth up/down, device activity,
  latency/loss charts, new devices, alerts, network health with plain-English
  explanations (event-driven via SSE).
- **Network map** — Internet → Router → AP/segments → clients, drawn only where
  evidence exists.
- **Traffic** — flow metadata table (5-tuple, bytes, timestamps), never content.
- **Router / Wi-Fi / Ethernet pages** — gateway, DHCP/DNS/WAN/uptime (optional
  authorized router API), SSID/BSSID/signal/channel/radio/security (**never
  Wi-Fi passwords**), adapter/link/IP/DNS details.
- **New-device alerts** with evidence and manual triage
  (Trusted / Unknown / Ignore / Investigate) — the app never blocks or attacks.
- **History** — sessions, IP/MAC/hostname changes, connectivity; filterable
  timeline with device/event/timestamp/details columns.
- **Reports** — HTML, JSON, CSV, PDF (summary, inventory, history, health,
  alerts, traffic stats).
- **Correlation engine** with provenance
  (`Field → Value → Source → Timestamp → Confidence`); conflicts are preserved,
  never silently overwritten.
- **Windows integration** — works without admin; per-feature elevation with
  explanation when a specific API needs it.
- **Global search** (`Ctrl+K`) by IP/MAC/hostname/vendor/name/type/status.
- **Dark/light themes**, tooltips, accessible tables, full keyboard navigation.
- **Offline mode** (local-only defaults), optional encrypted notes, configurable
  retention, export/delete history, clear logs.

## Install (Windows)

Download `LANWatcher-Setup-1.0.0.exe` from Releases, or build:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-build.txt
./installer/build.sh            # tests + dist/LANWatcher.exe + setup exe (Windows)
./installer/build_zipapp.sh    # or portable dist/lanwatcher.pyz (any OS)
```

## Run from source

```bash
.venv/bin/pip install psutil cryptography pywebview   # optional but recommended
.venv/bin/python -m lanwatcher                        # desktop window
.venv/bin/python -m lanwatcher --serve --open-browser # browser mode
.venv/bin/python -m lanwatcher --demo                 # demo data
```

## Tests

```bash
.venv/bin/python -m pytest tests/python -q   # 128 Python tests
npm install && npm test                      # 22 frontend tests (node:test + jsdom)
```

Test suites include discovery, IP/MAC correlation, offline detection,
new-device detection, DB operations, router integration, Wi-Fi/Ethernet
parsing, traffic metadata, report generation, UI state transitions, failure
recovery — all against a deterministic **simulated network provider** (no real
traffic is touched).

## Documentation

- [User guide](docs/USER-GUIDE.md)
- [Privacy & security design](docs/PRIVACY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Router API integration](docs/ROUTER-API.md)

## Explicitly prohibited (and not implemented)

Credential theft · covert surveillance · packet-content interception ·
unauthorized router access · persistence · evasion · automatic attacks.

## License

MIT © 2026 [Mahdi](LICENSE)
