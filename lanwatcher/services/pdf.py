"""Minimal dependency-free PDF writer for text reports (Helvetica, A4).

Produces valid PDF 1.4 with proper xref table. Supports headings, paragraphs,
simple tables and page breaks. Sufficient for inventory/history reports.
"""

from __future__ import annotations

from typing import Optional

PAGE_W, PAGE_H = 595.28, 841.89  # A4 points
MARGIN = 48.0
LEADING = 14.0
FONT_SIZE = 10.0
TITLE_SIZE = 18.0
H_SIZE = 13.0


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


class PdfDocument:
    def __init__(self, title: str = "Report"):
        self.title = title
        self.pages: list = []
        self._ops: list = []
        self._y = PAGE_H - MARGIN
        self._new_page()

    def _new_page(self) -> None:
        if self._ops:
            self.pages.append(self._ops)
        self._ops = []
        self._y = PAGE_H - MARGIN

    def _ensure(self, height: float) -> None:
        if self._y - height < MARGIN:
            self._new_page()

    def title_text(self, text: str) -> None:
        self._ensure(30)
        self._ops.append(f"BT /F1 {TITLE_SIZE} Tf 0 Td ET")
        self._ops.append(f"BT /F1 {TITLE_SIZE} Tf {MARGIN} {self._y} Td ({_esc(text)}) Tj ET")
        self._y -= 28

    def heading(self, text: str) -> None:
        self._ensure(24)
        self._y -= 8
        self._ops.append(f"BT /F1 {H_SIZE} Tf {MARGIN} {self._y} Td ({_esc(text)}) Tj ET")
        self._y -= 8
        self._line()
        self._y -= 12

    def paragraph(self, text: str, size: float = FONT_SIZE) -> None:
        max_chars = int((PAGE_W - 2 * MARGIN) / (size * 0.5))
        words = (text or "").split()
        line = ""
        lines = []
        for w in words:
            if len(line) + len(w) + 1 > max_chars:
                lines.append(line)
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            lines.append(line)
        if not lines:
            lines = [""]
        for ln in lines:
            self._ensure(LEADING)
            self._ops.append(f"BT /F1 {size} Tf {MARGIN} {self._y} Td ({_esc(ln)}) Tj ET")
            self._y -= LEADING

    def spacer(self, h: float = 8.0) -> None:
        self._y -= h

    def kv(self, key: str, value) -> None:
        self.paragraph(f"{key}: {value if value not in (None, '') else '—'}")

    def table(self, headers: list, rows: list, widths: Optional[list] = None) -> None:
        n = max(1, len(headers))
        avail = PAGE_W - 2 * MARGIN
        widths = widths or [avail / n] * n
        self._ensure(20)
        x = MARGIN
        for i, h in enumerate(headers):
            self._ops.append(f"BT /F1 {FONT_SIZE} Tf {x} {self._y} Td ({_esc(str(h)[: int(widths[i] / 5)])}) Tj ET")
            x += widths[i]
        self._y -= 6
        self._line()
        self._y -= 10
        for row in rows:
            self._ensure(LEADING)
            x = MARGIN
            for i, cell in enumerate(row[:n]):
                text = str(cell) if cell is not None else "—"
                max_c = max(3, int(widths[i] / 5.1))
                if len(text) > max_c:
                    text = text[: max_c - 1] + "…"
                self._ops.append(f"BT /F1 {FONT_SIZE} Tf {x} {self._y} Td ({_esc(text)}) Tj ET")
                x += widths[i]
            self._y -= LEADING

    def _line(self) -> None:
        self._ops.append(f"{MARGIN} {self._y} m {PAGE_W - MARGIN} {self._y} l S")

    def render(self) -> bytes:
        if self._ops:
            self.pages.append(self._ops)
            self._ops = []
        objects: list = []  # 1-indexed

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        page_ids = []
        content_ids = []
        # reserve catalog/pages ids later; create content streams first
        content_bodies = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("latin-1", errors="replace")
            content_bodies.append(stream)
        pages_obj_id = len(objects) + len(content_bodies) * 2 + 1
        for stream in content_bodies:
            cid = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
            content_ids.append(cid)
            pid = add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 842] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
                % (pages_obj_id, cid, font_id)
            )
            page_ids.append(pid)
        kids = " ".join(f"{i} 0 R" for i in page_ids)
        real_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
        assert real_pages_id == pages_obj_id
        catalog_id = add(f"<< /Type /Catalog /Pages {real_pages_id} 0 R >>".encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref_pos = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
        ).encode()
        return bytes(out)


def render_simple_pdf(title: str, sections: list) -> bytes:
    """sections: list of (heading, [ ('kv', k, v) | ('p', text) | ('table', headers, rows) ])"""
    doc = PdfDocument(title=title)
    doc.title_text(title)
    for heading, items in sections:
        doc.heading(heading)
        for item in items:
            if item[0] == "kv":
                doc.kv(item[1], item[2])
            elif item[0] == "p":
                doc.paragraph(item[1])
            elif item[0] == "table":
                doc.table(item[1], item[2])
        doc.spacer(6)
    return doc.render()
