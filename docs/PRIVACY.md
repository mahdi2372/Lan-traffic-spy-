# Privacy & Security Design

LAN Watcher is a monitoring tool for networks you own or are authorized to
administer. Its privacy posture is enforced in code and by default.

## What is collected (metadata only)

- Device identity: IP (v4/v6), MAC + OUI vendor, hostname, interface, category,
  connection type, first/last seen, online state, latency/packet-loss from
  safe ICMP probes.
- Activity **metadata**: bytes sent/received, flow records (src/dst IP+port,
  protocol, timestamps, byte counts, duration), aggregate rates.
- Network state: interfaces, ARP/neighbour tables, Wi-Fi association metadata
  (SSID/BSSID/signal/channel/radio/rates/security mode), router/gateway facts.
- History: sessions, IP/MAC/hostname changes, connectivity changes.

## What is NEVER collected

- Packet payloads of any kind
- Passwords, cookies, auth tokens, keystrokes
- Chat / email / browsing content
- HTTPS plaintext (encrypted traffic contents are not intercepted)
- Files transferred on the network
- Wi-Fi passwords / PSKs (no code path reads WLAN key material)
- Remote credentials beyond optional router API login **you explicitly supply**

## Local-only by default

- No telemetry, no analytics, no auto-update pings, no hidden background
  communications. `privacy.telemetry` is hard-locked `False` in code.
- `privacy.local_only_mode = true` by default: the documented internet
  reachability probe and DNS probe are skipped or local-only.
- The optional internet probe (off by default) contacts a single configurable
  URL (default: Microsoft NCSI `http://www.msftconnecttest.com/connecttest.txt`)
  with a GET and no user data. The UI discloses it and provides one-click disable.
- Discovery is strictly local: ARP/ND tables, Windows commands, your router's
  API **only** with credentials you provide.

## Data at rest

- Everything lives in `%LOCALAPPDATA%\LAN Watcher` (or `~/.local/share/LAN Watcher`).
- `privacy.encrypt_notes = true` by default: user notes are encrypted with
  **Windows DPAPI** (`dpapi:` values) or a Fernet key stored beside the DB with
  restrictive permissions (`aes:` values as fallback where DPAPI is absent).
- Router passwords are stored only via SecureStore, masked in the UI, excluded
  from API responses and logs.

## Retention & deletion

Configurable automatic purging (Settings → Retention): flows 7 days, events 90
days, sessions/alerts 180 days (values adjustable). Plus explicit tools:

- **Export history** (JSON/CSV) to a location you choose
- **Delete history** (events, sessions, flows, alerts; keeps device names/notes)
- **Clear logs**
- **Reset settings**

Deleting the app data folder removes everything; the uninstaller deliberately
does not silently delete user data.

## UI visibility

Monitoring is always visible to the operator: the status bar shows scan state,
last scan time, live bandwidth and health; Settings → Diagnostics shows every
collector with run counts, errors and last-run times. There is no hidden tray
process or background service outside the running app.

## Local API security

- Binds `127.0.0.1` only (a `--host` override exists for controlled preview
  setups and logs a warning).
- Every `/api` call requires a per-run random token (48 chars, `secrets`),
  delivered via `Authorization: Bearer`, `X-LAN-Watcher-Token`, or `?token=`
  (for the window bootstrap and file downloads). Stored `0600` in `api.token`.
- CSRF guard: an `Origin` header, when present, must match the request `Host`.
- CSP `default-src 'self'`, path-traversal rejection for static files,
  read-only `/` + `GET` serving of the bundled UI.
- SSE event stream is token-gated like the rest of the API.

## Threat model notes

- The app defends against: other local users reading the monitoring data
  (token + file permissions), malicious web pages calling the local API
  (token + origin checks), accidental data leaks (local-only defaults,
  no telemetry).
- It does **not** defend against an administrator-level attacker on the same
  machine (DPAPI is per-user).
- Prohibited by design: credential theft, covert surveillance, packet-content
  interception, unauthorized router access, persistence mechanisms, evasion
  techniques, automatic attacks or disconnection of devices.
