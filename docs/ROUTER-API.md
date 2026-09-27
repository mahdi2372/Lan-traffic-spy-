# Optional Router API Integration

LAN Watcher discovers router facts locally (gateway, routes, firewall profile,
DNS) without any credentials. For **live DHCP leases/clients and WAN/uptime
data**, it can integrate with your router's own admin API — only if you opt in.

## Rules

- **Authorized access only.** Use credentials for a router you administer.
- Credentials are entered by you in Settings → Router, stored with DPAPI
  encryption where available, masked in the UI, never written to logs or
  reports, and never sent anywhere except the router base URL you configure.
- LAN Watcher never brute-forces, scans for, or bypasses router authentication.

## Setup

1. Open **Settings → Router integration**.
2. Enter the router **base URL** (e.g. `http://192.168.1.1` — LAN/local only).
3. Choose **auth type** (`none`, `basic`, or `token`) and enter username /
   password or token.
4. Click **Test connection**. The result and any error follow the
   What/Why/What-to-do format.

## Endpoints (configurable)

| Purpose | Default path | Method | Notes |
| --- | --- | --- | --- |
| Status (model, WAN, uptime) | `/api/status` | GET | JSON |
| DHCP leases/clients | `/api/dhcp/leases` | GET | JSON list |

Field names are mapped liberally (e.g. `ip`/`address`, `mac`/`hwaddr`,
`hostname`/`name`, `expires`/`lease_time`) and anything unrecognized is kept as
metadata with source attribution `router:<path>`.

## Compatibility notes

Consumer routers differ widely. If your router has no HTTP API, leave this
disabled — all local discovery continues to work. Popular alternatives:

- Routers exposing **UPnP/SSDP** summaries are labeled as such in the UI.
- OpenWrt: enable `rpcd` with a read-only account and use `/cgi-bin/luci/rpc/…`
  or the ubus JSON-RPC endpoint; map the paths above in Settings.
- pfSense/OPNsense: use a read-only API key and set the base URL to the LAN
  admin URL.

Unrecognized responses are surfaced verbatim in Diagnostics — nothing is
inferred or invented.
