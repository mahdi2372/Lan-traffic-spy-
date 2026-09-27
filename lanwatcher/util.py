"""Small shared helpers: time, addresses, platform paths, command execution."""

from __future__ import annotations

import ipaddress
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------- time


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    """ISO-8601 UTC timestamp with 'Z' suffix (second precision, lexicographically sortable)."""
    if dt is None:
        dt = utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def iso_to_epoch(value: str) -> float:
    dt = parse_iso(value)
    return dt.timestamp() if dt else 0.0


def epoch_to_iso(ts: float) -> str:
    return iso(datetime.fromtimestamp(ts, tz=timezone.utc))


def seconds_since(value: str, now: Optional[datetime] = None) -> float:
    dt = parse_iso(value)
    if not dt:
        return float("inf")
    now = now or utc_now()
    return (now - dt).total_seconds()


# ---------------------------------------------------------------- addresses

_MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$|^[0-9a-f]{12}$")


def normalize_mac(value: Optional[str]) -> Optional[str]:
    """Return lowercase 12-hex-digit MAC ('aabbccddeeff') or None when invalid.

    Accepts Cisco triple-dot ('aabb.ccd d.eeff'), colon and dash separators.
    """
    if not value:
        return None
    v = value.strip().lower().replace(":", "").replace("-", "").replace(".", "").replace(" ", "")
    if len(v) == 16 and _MAC_RE.match(v) is None:
        return None
    if len(v) != 12 or any(c not in "0123456789abcdef" for c in v):
        return None
    if v == "000000000000":
        return None
    return v


def mac_display(norm: Optional[str]) -> Optional[str]:
    if not norm or len(norm) != 12:
        return norm
    return ":".join(norm[i : i + 2] for i in range(0, 12, 2))


def mac_oui(norm: Optional[str]) -> Optional[str]:
    return norm[:6] if norm and len(norm) == 12 else None


def is_valid_ip(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def is_valid_ipv4(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        return isinstance(ipaddress.ip_address(value.strip()), ipaddress.IPv4Address)
    except ValueError:
        return False


def is_valid_ipv6(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        return isinstance(ipaddress.ip_address(value.strip()), ipaddress.IPv6Address)
    except ValueError:
        return False


def ip_family(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return "ipv4" if isinstance(addr, ipaddress.IPv4Address) else "ipv6"


def parse_subnet(value: str) -> Optional[ipaddress._BaseNetwork]:
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def ip_in_networks(ip: str, networks: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in networks:
        if addr in net:
            return True
    return False


def is_link_local(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.strip()).is_link_local
    except ValueError:
        return False


def is_loopback(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.strip()).is_loopback
    except ValueError:
        return False


def is_private_ip(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.strip()).is_private
    except ValueError:
        return False


def is_multicast(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.strip()).is_multicast
    except ValueError:
        return False


def is_broadcast_mac(mac_norm: Optional[str]) -> bool:
    return mac_norm == "ffffffffffff"


# ---------------------------------------------------------------- platform paths


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def app_dir(app_folder: str = "LANWatcher") -> Path:
    """Per-user writable directory for config/data. Never requires elevation."""
    env = os.environ.get("LANWATCHER_HOME")
    if env:
        p = Path(env)
    elif is_windows():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / app_folder
    elif is_macos():
        p = Path.home() / "Library" / "Application Support" / app_folder
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        p = Path(base) / "lanwatcher"
    p.mkdir(parents=True, exist_ok=True)
    return p


def format_bps(bits_per_second: float) -> str:
    units = ["bps", "kbps", "Mbps", "Gbps", "Tbps"]
    v = float(max(0.0, bits_per_second))
    for unit in units:
        if v < 1000 or unit == units[-1]:
            return f"{v:.0f} {unit}" if unit == "bps" else f"{v:.1f} {unit}"
        v /= 1000.0
    return f"{v:.1f} Tbps"


def format_bytes(n: Optional[float]) -> str:
    if n is None:
        return "—"
    v = float(max(0.0, n))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} TB"


def format_ms(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f} ms"


# ---------------------------------------------------------------- command execution


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    error: Optional[str] = None


class CommandRunner:
    """Runs local OS commands without a shell. Injectable for tests.

    Commands run with bounded timeouts and no environment mutation so a
    misbehaving collector can never hang the application.
    """

    def __init__(self, runner: Optional[Callable[..., CommandResult]] = None, default_timeout: float = 15.0):
        self._runner = runner
        self._timeout = default_timeout

    def run(self, args: list, timeout: Optional[float] = None) -> CommandResult:
        if self._runner is not None:
            return self._runner(args)
        if not args or shutil.which(args[0]) is None:
            return CommandResult(False, "", "", -1, error=f"command not found: {args[0] if args else '?'}")
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or self._timeout,
                shell=False,
            )
            return CommandResult(proc.returncode == 0, proc.stdout or "", proc.stderr or "", proc.returncode)
        except subprocess.TimeoutExpired:
            return CommandResult(False, "", "", -1, error=f"timeout after {timeout or self._timeout}s")
        except OSError as exc:  # pragma: no cover - environment dependent
            return CommandResult(False, "", "", -1, error=str(exc))


def host_label() -> str:
    return platform.node() or "this-pc"
