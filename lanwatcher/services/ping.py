"""ICMP reachability probing without elevation.

Windows: IcmpSendEcho via iphlpapi (no admin required).
POSIX: system ping command with strict timeout.
Injectable for tests. Rate limiting is applied by callers.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from ..util import is_windows, is_valid_ip


@dataclass
class PingResult:
    target: str
    ok: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class Pinger:
    """Single ICMP echo. Never raises."""

    def __init__(self, runner=None):
        self._runner = runner  # for tests: callable(target, count) -> list[PingResult]

    def ping(self, target: str, timeout_s: float = 1.0) -> PingResult:
        if self._runner is not None:
            try:
                return self._runner(target, timeout_s)
            except Exception as exc:  # noqa: BLE001
                return PingResult(target, False, error=str(exc))
        if not is_valid_ip(target) and "." not in target:
            return PingResult(target, False, error="invalid target")
        try:
            if is_windows():
                return self._ping_windows(target, timeout_s)
            return self._ping_posix(target, timeout_s)
        except Exception as exc:  # noqa: BLE001
            return PingResult(target, False, error=str(exc))

    def ping_stats(self, target: str, count: int = 3, timeout_s: float = 1.0) -> tuple:
        """Return (avg_latency_ms | None, loss_pct)."""
        latencies = []
        sent = 0
        for _ in range(max(1, count)):
            sent += 1
            r = self.ping(target, timeout_s)
            if r.ok and r.latency_ms is not None:
                latencies.append(r.latency_ms)
        loss = 100.0 * (sent - len(latencies)) / sent
        avg = sum(latencies) / len(latencies) if latencies else None
        return avg, loss

    # -------------------------------------------------- windows

    @staticmethod
    def _ping_windows(target: str, timeout_s: float) -> PingResult:  # pragma: no cover - windows only
        import socket

        icmp = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
        ws2 = ctypes.windll.ws2_32  # type: ignore[attr-defined]
        try:
            addr = socket.inet_pton(socket.AF_INET, target)
        except OSError:
            return PingResult(target, False, error="ipv6 ping unsupported in lightweight probe")
        class _Echo(ctypes.Structure):
            _fields_ = [
                ("DataSize", ctypes.c_ushort),
                ("Reserved1", ctypes.c_ushort),
                ("Data", ctypes.c_void_p),
                ("Padding", ctypes.c_byte * 8),
            ]
        buf = ctypes.create_string_buffer(b"lanwatcher-echo", 16)
        reply_buf = ctypes.create_string_buffer(32 + 8)
        handle = icmp.IcmpCreateFile()
        if handle == -1:
            return PingResult(target, False, error="IcmpCreateFile failed")
        try:
            status = icmp.IcmpSendEcho(
                handle,
                int.from_bytes(addr, "big") if False else ctypes.c_ulong(socket.ntohl(int.from_bytes(addr, "big"))),
                buf,
                16,
                None,
                reply_buf,
                32 + 8,
                int(timeout_s * 1000),
            )
            if status:
                # reply: 8-byte ICMP echo reply header + 28-byte IP header layout;
                # RTT lives at bytes 8-11 of ICMP_ECHO_REPLY as ULONG RoundTripTime.
                rtt = int.from_bytes(reply_buf.raw[8:12], "little")
                return PingResult(target, True, latency_ms=float(rtt))
            return PingResult(target, False, error="timeout")
        finally:
            icmp.IcmpCloseHandle(handle)

    # -------------------------------------------------- posix

    @staticmethod
    def _ping_posix(target: str, timeout_s: float) -> PingResult:
        try:
            proc = subprocess.run(
                ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), target],
                capture_output=True,
                text=True,
                timeout=timeout_s + 2,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PingResult(target, False, error=str(exc))
        if proc.returncode != 0:
            return PingResult(target, False, error="no reply")
        m = re.search(r"time[=<]([\d.]+)\s*ms", proc.stdout)
        if m:
            return PingResult(target, True, latency_ms=float(m.group(1)))
        return PingResult(target, True, latency_ms=timeout_s * 1000.0)
