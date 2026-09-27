# LAN Watcher — User Guide

**LAN Watcher** is a privacy-preserving, offline-first desktop application for
monitoring and managing a local network you own or are authorized to administer.
Everything runs and stays on your machine.

## Contents

1. [Getting started](#getting-started)
2. [Dashboard](#dashboard)
3. [Devices](#devices)
4. [Device details](#device-details)
5. [Network Map](#network-map)
6. [Traffic](#traffic)
7. [Router](#router)
8. [Wi-Fi](#wi-fi)
9. [Ethernet](#ethernet)
10. [Alerts](#alerts)
11. [History](#history)
12. [Reports](#reports)
13. [Settings](#settings)
14. [Global search](#global-search)
15. [How discovery works](#how-discovery-works)
16. [Network health](#network-health)
17. [Troubleshooting](#troubleshooting)
18. [Keyboard shortcuts](#keyboard-shortcuts)

## Getting started

### Windows (installer)

1. Run `LANWatcher-Setup-1.0.0.exe`.
2. Launch **LAN Watcher** from the Start menu.
3. The app window opens on the **Dashboard**. Discovery starts automatically.

### From source

```bash
python -m venv .venv
.venv/bin/pip install psutil cryptography pywebview   # psutil enables live bandwidth
.venv/bin/python -m lanwatcher                        # desktop window
.venv/bin/python -m lanwatcher --serve --open-browser # or plain browser mode
.venv/bin/python -m lanwatcher --demo                 # demo data (testing)
```

Data is stored per user in `%LOCALAPPDATA%\LAN Watcher` (Windows) or
`~/.local/share/LAN Watcher` (Linux): `lanwatcher.db` (SQLite), `settings.json`,
`secret.key`, `api.token`, `logs/`.

## Dashboard

- **Stat cards**: total / online / offline devices, Wi-Fi / Ethernet / unknown
  link counts, new devices today, open alerts.
- **Live charts**: total/upload/download bandwidth, device activity over time,
  gateway latency, packet loss. Charts update through the event stream — no
  manual refresh.
- **Network health**: each check (gateway latency, packet loss, DNS, internet,
  LAN, DHCP, device availability) with a plain-English explanation of any
  degraded state. LAN Watcher reports what it measured — it never invents
  diagnoses.
- **Latest alerts** with one-click triage.

The status bar (bottom) always shows: DB size, CPU/RAM use, whether scanning is
active and when the last scan finished, live bandwidth, and the health state.

## Devices

The sortable inventory table: **Status | Device | IP | MAC | Vendor | Type |
Upload | Download | Last seen**.

- **Search** across IP / MAC / hostname / vendor / user name / type / status.
- **Filters**: link type (Wi-Fi / Ethernet / Unknown) and online/offline.
- **Sort**: bandwidth, last seen, latency, name.
- **Group** by type or category.
- **Pin** favorites (always float to the top).
- **Rename** and add **notes** (open the device profile).
- Select rows and **Export CSV** for the selection.

Unknown link type means the connection type has not been established — LAN
Watcher never guesses Wi-Fi vs Ethernet without evidence.

## Device details

Click any device (or press Enter on its row) to open the profile:

- **Identity** — name, category, IPs, MAC + OUI vendor, connection type,
  interface, confidence, user notes.
- **Connectivity** — online state, online duration (current session), latency
  and packet loss from safe ICMP probes, first/last seen.
- **Network activity** — *metadata only*: bytes sent/received, flow count, top
  destination IPs and ports, protocols, timestamps. Payload contents are never
  captured or stored.
- **Provenance** — every fact with `Field → Value → Source → Timestamp →
  Confidence`. Conflicting observations are kept side by side, never silently
  overwritten.
- **Sessions** timeline and **edit / delete** controls.

## Network Map

Internet → Router/Gateway → segments & APs → clients. Wired / wireless /
unknown clients are drawn differently; the legend states that relationships are
shown **only where evidence exists**. If the path to a device is unknown, no
path is drawn.

## Traffic

- Live upload/download chart (host machine counters when `psutil` is available;
  simulated provider in demo mode).
- **Flow table (metadata only)**: source/destination IP + port, protocol,
  bytes, first/last seen. Filtering and protocol display.
- What LAN Watcher records — and what it never records — is spelled out on the
  page.

## Router

Gateway/router summary: IP, hostname, LAN subnet, DHCP range, model/vendor,
DNS servers, IPv4/IPv6 info, WAN status, uptime. Includes the Windows system
view (default gateway, firewall profile, DHCP server, lease times) and the
status of optional **authorized router API integration**.

To enable live DHCP lease/client data from your router, see
[ROUTER-API.md](ROUTER-API.md). Without credentials, the page shows everything
that can be gathered locally and says so.

## Wi-Fi

Your wireless association metadata: SSID, BSSID, signal %, channel, radio type,
link rates, band (2.4/5/6 GHz), security mode — plus visible networks when
available. **Wi-Fi passwords are never read or displayed.** (There is no code
path in the application that reads WLAN key material.)

## Ethernet

Per wired adapter: name, description, MAC, link state, link speed, MTU, IPv4/
IPv6 + prefix, gateway, DNS, network profile, uptime.

## Alerts

New-device detection compares live observations against your inventory. Each
alert carries the evidence: IP, MAC, vendor, time, plus any observed changes
(IP change, online/offline, conflicts).

Mark each alert: **Trusted / Unknown / Ignore / Investigate** (or use the quick
buttons). "Investigate" keeps the alert open. LAN Watcher **never** blocks,
disconnects, or attacks any device — alerting and marking are the only actions.

## History

Timeline of sessions, IP/MAC/hostname changes, bandwidth records, and
connectivity changes — `Device → Event → Time → Details`, with filters for
today / 24 h / 7 days / 30 days / custom range and per-device filtering.

## Reports

Choose a **time range** (24 h / 7 days / 30 days / all) and one of four formats
— **HTML, JSON, CSV, PDF**. Every report is comprehensive and contains:

| Section | Contents |
| --- | --- |
| Network summary | device counts, bandwidth totals, health snapshot |
| Device inventory | all devices with Status/Device/IP/MAC/Vendor/Type, plus Wi-Fi / Ethernet / Unknown totals |
| Device history | recent sessions and events (Device → Event → Timestamp) |
| Network health | all checks with explanations |
| Alerts | alerts with current triage state |
| Traffic statistics | flows observed, bytes recorded (metadata only) |

Reports contain metadata only (same privacy rules as the rest of the app). The
same data can be exported from **Settings → Tools → Export history & data
(JSON)**.

## Settings

- **General** — scan interval (rate-limited; minimum 5 s), offline threshold,
  latency probe toggle.
- **Privacy & security** — local-only mode, encrypt notes at rest (DPAPI),
  visible internet reachability probe (off by default), telemetry (permanently
  off).
- **Retention** — automatic purging of flows / events / sessions / alerts after
  the configured days.
- **Router integration** — optional credentials for your router's API
  (stored encrypted where DPAPI is available; masked in the UI; never logged).
- **Alerts** — which events create alerts.
- **Diagnostics** — version, platform, CPU/RAM/threads/uptime, DB size, and the
  per-collector status table (runs / errors / last run / last error).
- **Tools** — run scan now, **Export history & data (JSON)**, **clear logs**,
  **delete history**, purge retention now, reset settings.

## Global search

`Ctrl+K` (or click the search box) searches devices by IP, MAC (any separator
style), hostname, vendor, name, type, or status. Enter jumps straight to the
device profile.

## How discovery works

Collectors run in order; each fails independently and is reported in
Settings → Diagnostics:

| Collector | Source | Elevation |
| --- | --- | --- |
| WindowsNetwork | `ipconfig /all`, `netstat -rn` | none |
| ARP | `arp -a` | none |
| Neighbor | `netsh interface ipv4/ipv6 show neighbors` | none |
| DHCP | `netsh interface ipv4 show dnsservers`, optional router API | none / router login |
| Router | `route print`, firewall profile, optional router API | none / router login |
| WiFi | `netsh wlan show interfaces/networks` | none |
| Ethernet | `Get-NetAdapter` (when available), `ipconfig` | none |
| Flow | interface counters (`psutil`) + safe per-device probes | none |
| Health | ICMP ping, DNS resolution, documented connectivity probe | ping may need a firewall rule |

Findings are merged by the correlation engine: MAC + IP + hostname + vendor +
interface + timestamps. Conflicts are preserved with source attribution
(confidence: high / medium / low). Everything works without administrator
rights; where a Windows API needs elevation, the app **explains why and asks
for that feature only**.

Probing is rate-limited (configurable interval, capped concurrency, exponential
backoff on errors) — the app never floods the network.

## Network health

- Gateway latency & packet loss (ICMP, safe probes)
- DNS resolution time
- Internet reachability (optional, documented HTTP probe — off by default)
- LAN availability (share of known devices answering)
- DHCP availability (server reachability/lease info when known)
- Device availability (online/offline by the configured threshold)

Degraded states come with explanations of what was measured and what it may
mean — no invented root causes.

## Troubleshooting

Every error in the UI is shown as **What happened → Why it may happen → What
you can do**. Common cases:

| Symptom | What to do |
| --- | --- |
| Router unavailable / no DHCP data | Configure router credentials in Settings → Router, or ignore — local discovery continues. |
| Permission denied | Follow the elevation prompt for the specific feature, or continue without it. |
| Wi-Fi / Ethernet API unavailable | The page explains which command failed; wired/wireless types stay "Unknown" until real data arrives. |
| Firewall blocks probes | Allow LAN Watcher in Windows Firewall, or disable latency probes in Settings. |
| Timeouts / invalid subnet | Check the scan interval and network profile; scans pause automatically on repeated failures. |
| Database failure | Error dialog offers "retry"; the DB is local SQLite and recreated-safe (backups via export). |

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `Ctrl+K` | Global search |
| `Ctrl+B` | Toggle dark/light theme |
| `Tab` / `Enter` / arrow keys | Full keyboard navigation of tables, buttons, dialogs |
| `Esc` | Close dialogs |
