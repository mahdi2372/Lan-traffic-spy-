"""Report generation: HTML, JSON, CSV, PDF.

All reports include: network summary, device inventory, Wi-Fi/Ethernet/unknown
split, device history, network health, alerts and traffic statistics.
"""

from __future__ import annotations

import csv
import html
import io
import json
from typing import Optional

from .. import APP_NAME, __version__
from ..db import Repositories
from ..util import format_bytes, format_bps, iso
from .history import HistoryService, resolve_range
from .pdf import render_simple_pdf


class ReportService:
    def __init__(self, repos: Repositories, history: Optional[HistoryService] = None):
        self.repos = repos
        self.history = history or HistoryService(repos)

    # ============================================================ data

    def build_model(self, preset: str = "7d") -> dict:
        since, until = resolve_range(preset)
        devices = self.repos.devices.list_all()
        counts = self.repos.devices.counts()
        alerts = self.repos.alerts.list(since=since, limit=1000)
        events = self.repos.events.list(since=since, limit=2000)
        flows = self.repos.flows.totals_since(since) if since else {"flows": 0, "bytes_total": 0}
        router = self.repos.router.latest() or {}
        ifaces = self.repos.interfaces.latest()
        wifi = self.repos.wifi.latest()
        return {
            "app": APP_NAME,
            "version": __version__,
            "generated_at": iso(),
            "range": {"preset": preset, "since": since, "until": until},
            "summary": counts,
            "devices": devices,
            "wifi_devices": [d for d in devices if d.get("connection_type") == "wifi"],
            "ethernet_devices": [d for d in devices if d.get("connection_type") == "ethernet"],
            "unknown_devices": [d for d in devices if d.get("connection_type") not in ("wifi", "ethernet")],
            "history": events,
            "alerts": alerts,
            "traffic": flows,
            "router": {k: v for k, v in router.items() if k not in ("raw_json", "dhcp_leases_json")},
            "interfaces": [i for i in ifaces],
            "wifi_links": wifi,
            "health": self.repos.settings.get("last_health"),
        }

    # ============================================================ JSON

    def to_json(self, model: dict) -> str:
        return json.dumps(model, indent=2, default=str)

    # ============================================================ CSV

    def to_csv(self, model: dict) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([f"{model['app']} report", model["generated_at"]])
        w.writerow([])
        w.writerow(["Summary"])
        for k, v in (model.get("summary") or {}).items():
            w.writerow([k, v])
        w.writerow([])
        w.writerow(
            ["Status", "Device", "IPv4", "IPv6", "MAC", "Vendor", "Type", "Category", "First Seen", "Last Seen", "Trust"]
        )
        for d in model.get("devices") or []:
            w.writerow(
                [
                    "online" if d.get("online") else "offline",
                    d.get("friendly_name") or d.get("hostname") or "",
                    d.get("ipv4") or "",
                    d.get("ipv6") or "",
                    d.get("mac") or "",
                    d.get("vendor") or "",
                    d.get("connection_type") or "unknown",
                    d.get("category") or "",
                    d.get("first_seen") or "",
                    d.get("last_seen") or "",
                    d.get("trust_state") or "unknown",
                ]
            )
        w.writerow([])
        w.writerow(["Alerts"])
        w.writerow(["Time", "Kind", "Severity", "State", "Message"])
        for a in model.get("alerts") or []:
            w.writerow([a.get("ts"), a.get("kind"), a.get("severity"), a.get("state"), a.get("message")])
        return buf.getvalue()

    # ============================================================ HTML

    def to_html(self, model: dict) -> str:
        def esc(x) -> str:
            return html.escape(str(x)) if x is not None else "—"

        rows = []
        for d in model.get("devices") or []:
            status = "online" if d.get("online") else "offline"
            rows.append(
                f"<tr><td><span class='pill {status}'>{status}</span></td><td>{esc(d.get('friendly_name') or d.get('hostname'))}"
                f"</td><td>{esc(d.get('ipv4'))}</td><td>{esc(d.get('mac'))}</td><td>{esc(d.get('vendor'))}"
                f"</td><td>{esc(d.get('connection_type'))}</td><td>{esc(d.get('first_seen'))}</td><td>{esc(d.get('last_seen'))}</td></tr>"
            )
        alert_rows = []
        for a in model.get("alerts") or []:
            alert_rows.append(
                f"<tr><td>{esc(a.get('ts'))}</td><td>{esc(a.get('kind'))}</td><td>{esc(a.get('severity'))}"
                f"</td><td>{esc(a.get('state'))}</td><td>{esc(a.get('message'))}</td></tr>"
            )
        hist_rows = []
        for e in (model.get("history") or [])[:200]:
            hist_rows.append(
                f"<tr><td>{esc(e.get('device_id'))}</td><td>{esc(e.get('event_type'))}</td><td>{esc(e.get('ts'))}"
                f"</td><td>{esc(json.dumps(e.get('details') or {}))}</td></tr>"
            )
        s = model.get("summary") or {}
        router = model.get("router") or {}
        traffic = model.get("traffic") or {}
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(model['app'])} Report</title>
<style>
body{{font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:2rem;color:#16202a;background:#f6f8fa}}
h1{{font-size:1.6rem}} h2{{font-size:1.15rem;border-bottom:2px solid #0b5;margin-top:2rem;padding-bottom:.3rem}}
.cards{{display:flex;gap:1rem;flex-wrap:wrap}} .card{{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:1rem 1.4rem;min-width:120px}}
.card b{{display:block;font-size:1.5rem}} table{{border-collapse:collapse;width:100%;background:#fff;margin-top:.6rem}}
th,td{{border:1px solid #d0d7de;padding:.4rem .6rem;font-size:.85rem;text-align:left}} th{{background:#eef2f5}}
.pill{{padding:.1rem .5rem;border-radius:99px;font-size:.75rem}} .pill.online{{background:#d1f7d1}} .pill.offline{{background:#fde2e2}}
footer{{margin-top:2rem;color:#556;font-size:.8rem}}
</style></head><body>
<h1>{esc(model['app'])} — Network Report</h1>
<p>Generated {esc(model['generated_at'])} · v{esc(model['version'])} · range: {esc(model['range']['preset'])} (since {esc(model['range']['since'])})</p>
<h2>Network Summary</h2>
<div class="cards">
<div class="card"><span>Total devices</span><b>{esc(s.get('total', 0))}</b></div>
<div class="card"><span>Online</span><b>{esc(s.get('online', 0))}</b></div>
<div class="card"><span>Offline</span><b>{esc(s.get('offline', 0))}</b></div>
<div class="card"><span>Wi-Fi</span><b>{esc(s.get('wifi', 0))}</b></div>
<div class="card"><span>Ethernet</span><b>{esc(s.get('ethernet', 0))}</b></div>
<div class="card"><span>Unknown</span><b>{esc(s.get('unknown', 0))}</b></div>
<div class="card"><span>Alerts</span><b>{esc(len(model.get('alerts') or []))}</b></div>
</div>
<h2>Router</h2>
<table><tr><th>Gateway</th><th>LAN subnet</th><th>WAN IP</th><th>WAN status</th><th>Model</th><th>DNS</th></tr>
<tr><td>{esc(router.get('gateway_ip'))}</td><td>{esc(router.get('lan_subnet'))}</td><td>{esc(router.get('wan_ip'))}</td>
<td>{esc(router.get('wan_status'))}</td><td>{esc(router.get('model') or router.get('vendor'))}</td><td>{esc(router.get('dns_servers'))}</td></tr></table>
<h2>Device Inventory</h2>
<table><tr><th>Status</th><th>Device</th><th>IPv4</th><th>MAC</th><th>Vendor</th><th>Type</th><th>First seen</th><th>Last seen</th></tr>
{''.join(rows) or '<tr><td colspan=8>No devices recorded</td></tr>'}</table>
<h2>Device History</h2>
<table><tr><th>Device</th><th>Event</th><th>Timestamp</th><th>Details</th></tr>
{''.join(hist_rows) or '<tr><td colspan=4>No history in range</td></tr>'}</table>
<h2>Alerts</h2>
<table><tr><th>Time</th><th>Kind</th><th>Severity</th><th>State</th><th>Message</th></tr>
{''.join(alert_rows) or '<tr><td colspan=5>No alerts in range</td></tr>'}</table>
<h2>Traffic Statistics</h2>
<p>Flows observed (metadata only): {esc(traffic.get('flows', 0))} · bytes recorded: {esc(format_bytes(traffic.get('bytes_total')))}</p>
<footer>LAN Watcher reports contain metadata only. No packet payloads, credentials or personal content are captured.</footer>
</body></html>"""

    # ============================================================ PDF

    def to_pdf(self, model: dict) -> bytes:
        s = model.get("summary") or {}
        router = model.get("router") or {}
        traffic = model.get("traffic") or {}
        sections = [
            (
                "Network Summary",
                [
                    ("kv", "Total devices", s.get("total", 0)),
                    ("kv", "Online", s.get("online", 0)),
                    ("kv", "Offline", s.get("offline", 0)),
                    ("kv", "Wi-Fi / Ethernet / Unknown", f"{s.get('wifi', 0)} / {s.get('ethernet', 0)} / {s.get('unknown', 0)}"),
                    ("kv", "Generated", model["generated_at"]),
                    ("kv", "Range", model["range"]["preset"]),
                ],
            ),
            (
                "Router",
                [
                    ("kv", "Gateway IP", router.get("gateway_ip")),
                    ("kv", "LAN subnet", router.get("lan_subnet")),
                    ("kv", "WAN IP", router.get("wan_ip")),
                    ("kv", "WAN status", router.get("wan_status")),
                    ("kv", "Model", router.get("model")),
                    ("kv", "DNS servers", router.get("dns_servers")),
                ],
            ),
            (
                "Device Inventory",
                [
                    (
                        "table",
                        ["Status", "Device", "IPv4", "MAC", "Vendor", "Type"],
                        [
                            [
                                "online" if d.get("online") else "offline",
                                d.get("friendly_name") or d.get("hostname") or d.get("device_id"),
                                d.get("ipv4") or "-",
                                d.get("mac") or "-",
                                d.get("vendor") or "-",
                                d.get("connection_type") or "unknown",
                            ]
                            for d in (model.get("devices") or [])[:40]
                        ],
                    )
                ],
            ),
            (
                "Wi-Fi / Ethernet / Unknown Devices",
                [
                    ("p", f"Wi-Fi: {len(model.get('wifi_devices') or [])} devices"),
                    ("p", f"Ethernet: {len(model.get('ethernet_devices') or [])} devices"),
                    ("p", f"Unknown: {len(model.get('unknown_devices') or [])} devices"),
                ],
            ),
            (
                "Device History (latest)",
                [
                    (
                        "table",
                        ["Device", "Event", "Timestamp"],
                        [
                            [e.get("device_id") or "-", e.get("event_type"), e.get("ts")]
                            for e in (model.get("history") or [])[:30]
                        ],
                    )
                ],
            ),
            (
                "Alerts",
                [
                    (
                        "table",
                        ["Time", "Kind", "State", "Message"],
                        [[a.get("ts"), a.get("kind"), a.get("state"), a.get("message")] for a in (model.get("alerts") or [])[:30]],
                    )
                ],
            ),
            (
                "Traffic Statistics",
                [
                    ("kv", "Flows observed", traffic.get("flows", 0)),
                    ("kv", "Bytes recorded", format_bytes(traffic.get("bytes_total"))),
                    ("p", "Metadata only - no payload contents are ever captured."),
                ],
            ),
        ]
        return render_simple_pdf(f"{model['app']} Report {model['generated_at']}", sections)

    # ============================================================ dispatcher

    def generate(self, fmt: str, preset: str = "7d") -> tuple:
        """Return (bytes|str, mime, filename)."""
        model = self.build_model(preset)
        stamp = iso().replace(":", "").replace("-", "")
        if fmt == "html":
            return self.to_html(model), "text/html", f"lanwatcher-report-{stamp}.html"
        if fmt == "json":
            return self.to_json(model), "application/json", f"lanwatcher-report-{stamp}.json"
        if fmt == "csv":
            return self.to_csv(model), "text/csv", f"lanwatcher-report-{stamp}.csv"
        if fmt == "pdf":
            return self.to_pdf(model), "application/pdf", f"lanwatcher-report-{stamp}.pdf"
        raise ValueError(f"unknown report format: {fmt}")

    def export_devices_csv(self, device_ids: list) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["device_id", "name", "hostname", "ipv4", "ipv6", "mac", "vendor", "type", "category", "first_seen", "last_seen", "trust_state", "notes"])
        for did in device_ids:
            d = self.repos.devices.get(did)
            if not d:
                continue
            w.writerow(
                [
                    d.get("device_id"), d.get("friendly_name") or "", d.get("hostname") or "",
                    d.get("ipv4") or "", d.get("ipv6") or "", d.get("mac") or "", d.get("vendor") or "",
                    d.get("connection_type"), d.get("category"), d.get("first_seen"), d.get("last_seen"),
                    d.get("trust_state"), d.get("notes") or "",
                ]
            )
        return buf.getvalue()
