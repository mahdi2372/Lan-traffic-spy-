#!/usr/bin/env python3
"""Generate installer/lanwatcher.ico without external dependencies.

Draws a simple radar motif on a dark rounded square, encodes it as a
256x256 PNG and wraps it in a Windows .ICO container (PNG-in-ICO,
Vista+ format).  Deterministic output - regenerate any time with:

    python installer/make_icon.py
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 256
CX = CY = SIZE / 2

# Palette (r, g, b, a)
BG_TOP = (11, 18, 32, 255)       # dark navy
BG_BOTTOM = (16, 27, 48, 255)    # slightly lighter navy
RING = (34, 197, 94, 150)        # radar green, translucent
RING_BRIGHT = (52, 211, 153, 220)
SWEEP = (52, 211, 153, 70)       # radar sweep wedge
DOT = (110, 231, 183, 255)       # center dot
BLIP = (56, 189, 248, 235)       # cyan blip


def _lerp(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _rounded_square_mask(x, y, r=44):
    """1 inside the rounded square, 0 outside."""
    half = SIZE / 2 - 6
    dx = abs(x - CX) - (half - r)
    dy = abs(y - CY) - (half - r)
    if dx <= 0 and dy <= 0:
        return 1.0
    if dx <= r and dy <= r:
        d = math.hypot(max(dx, 0.0), max(dy, 0.0)) - r
        return max(0.0, min(1.0, -d))
    return 0.0


def pixel(x, y):
    """RGBA for device pixel (x, y) - sampled at pixel center."""
    px, py = x + 0.5, y + 0.5
    mask = _rounded_square_mask(px, py)
    if mask <= 0:
        return (0, 0, 0, 0)

    # vertical gradient background
    col = _lerp(BG_TOP, BG_BOTTOM, py / SIZE)
    dist = math.hypot(px - CX, py - CY)

    # radar sweep wedge (points up-right)
    ang = math.atan2(CY - py, px - CX)  # 0 = +x, positive up
    in_sweep = 0.0
    if 10 < dist < 100 and -0.15 <= ang <= 1.05:
        in_sweep = 1.0 - min(1.0, (ang + 0.15) / 1.2 * 0.7)
    if in_sweep:
        col = _blend(col, SWEEP, in_sweep * 0.9)

    # concentric range rings at 32 / 64 / 96 px
    for r in (32, 64, 96):
        d = abs(dist - r)
        if d < 1.6:
            strength = (1.0 - d / 1.6)
            col = _blend(col, RING_BRIGHT if r == 64 else RING, strength)

    # centre dot
    if dist < 9:
        col = _blend(col, DOT, min(1.0, (9 - dist) / 2.0))

    # target blips on the rings
    for bx, by in ((CX + 62, CY - 40), (CX - 44, CY + 50), (CX + 20, CY - 88)):
        d = math.hypot(px - bx, py - by)
        if d < 7:
            col = _blend(col, BLIP, min(1.0, (7 - d) / 2.0))

    # anti-alias the whole icon against the transparent outside
    return (col[0], col[1], col[2], round(col[3] * mask))


def _blend(base, over, t):
    """Source-over blend of `over` onto `base` with opacity t (0..1)."""
    a = over[3] / 255.0 * t
    out_a = a + base[3] / 255.0 * (1 - a)
    if out_a <= 0:
        return (0, 0, 0, 0)
    rgb = tuple(round((over[i] * a + base[i] * base[3] / 255.0 * (1 - a)) / out_a) for i in range(3))
    return (rgb[0], rgb[1], rgb[2], round(out_a * 255))


def png_bytes(size=SIZE):
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        for x in range(size):
            raw.extend(pixel(x, y))
    body = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", body) + chunk(b"IEND", b""))


def ico_bytes(size=SIZE):
    png = png_bytes(size)
    # ICONDIR: reserved, type 1 (icon), count 1
    header = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY: w, h (0 => 256), colours 0, reserved, planes 1, bpp 32
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def main() -> None:
    out = Path(__file__).resolve().parent / "lanwatcher.ico"
    out.write_bytes(ico_bytes())
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
