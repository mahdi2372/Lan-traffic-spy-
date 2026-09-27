"""Flow collector: connection metadata only (5-tuple, state, duration).

NEVER captures packet payloads, keystrokes, browser content or application
data. Bytes are populated only from supported OS telemetry when the optional
per-flow byte probe is explicitly enabled (requires elevation on Windows) or
from router telemetry. Otherwise bytes stay null (shown as '—' in the UI).
"""

from __future__ import annotations

from typing import Optional

from ..models import FlowObservation
from ..util import is_link_local, is_loopback
from .base import Collector, CollectorResult


class FlowCollector(Collector):
    name = "flow"
    description = "Connection/flow metadata (5-tuple only, no payloads)"
    interval_s = 15.0

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        if not self.ctx.config.get("traffic.enabled", True):
            result.unavailable_reason = "traffic metadata collection is disabled in settings"
            result.ok = False
            return result
        try:
            import psutil
        except ImportError:
            result.ok = False
            result.unavailable_reason = "psutil is not installed"
            return result

        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            # Non-elevated Windows still exposes own-process sockets via netstat fallback
            conns = self._netstat_fallback()
            if conns is None:
                result.unavailable_reason = "connection enumeration denied (no elevation)"
                result.ok = False
                return result

        flows: list = []
        for c in conns:
            try:
                proto = "tcp" if c.type.name == "SOCK_STREAM" else "udp"
                l_ip, l_port = (c.laddr.ip, c.laddr.port) if c.laddr else (None, None)
                r_ip, r_port = (c.raddr.ip, c.raddr.port) if c.raddr else (None, None)
                if l_ip and (is_loopback(l_ip)):
                    continue
                if r_ip and is_loopback(r_ip):
                    continue
                state = getattr(c, "status", None)
                if proto == "tcp" and state in ("NONE",):
                    state = None
                flows.append(
                    FlowObservation(
                        src_ip=l_ip,
                        dst_ip=r_ip if r_ip and not is_link_local(r_ip) else r_ip,
                        src_port=l_port,
                        dst_port=r_port,
                        protocol=proto,
                        state=state.lower() if isinstance(state, str) else None,
                        source=self.name,
                    )
                )
            except Exception:  # noqa: BLE001 - one bad row must not kill the batch
                continue

        result.flows = flows
        result.system["flow_count"] = len(flows)

        # Optional per-flow byte counters (explicitly enabled, elevation-gated)
        if self.ctx.config.get("traffic.per_flow_bytes", False) and self.ctx.is_windows and self.ctx.is_elevated:
            try:
                from ..services.estats import enrich_flow_bytes

                enrich_flow_bytes(flows)
            except Exception as exc:  # noqa: BLE001
                result.system["per_flow_bytes_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def _netstat_fallback(self) -> Optional[list]:
        res = self.ctx.runner.run(["netstat", "-ano"])
        if not res.ok:
            return None
        from ..parsing.netstat import parse_netstat_ano

        class _C:  # small adapter mimicking psutil connection tuples
            def __init__(self, proto, lip, lport, rip, rport, state):
                self.type = type("T", (), {"name": "SOCK_STREAM" if proto == "TCP" else "SOCK_DGRAM"})()
                self.laddr = type("A", (), {"ip": lip, "port": lport})() if lip else None
                self.raddr = type("A", (), {"ip": rip, "port": rport})() if rip else None
                self.status = state or "NONE"

        out = []
        for r in parse_netstat_ano(res.stdout):
            out.append(_C(r.protocol, r.local_ip, r.local_port, r.remote_ip, r.remote_port, r.state))
        return out
