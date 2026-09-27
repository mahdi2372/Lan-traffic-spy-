/* Canvas time-series charts: multi-series line chart with gradient fill,
   gridlines, hover tooltip and dark/light aware colors. No dependencies. */

export class LineChart {
  constructor(canvas, { series = [], height = 180, unit = "", minY = 0 } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.series = series; // [{key, label, color, fill}]
    this.unit = unit;
    this.minY = minY;
    this.points = [];
    this.height = height;
    this.hoverIdx = -1;
    canvas.addEventListener("mousemove", (e) => this._onMove(e));
    canvas.addEventListener("mouseleave", () => { this.hoverIdx = -1; this.draw(); });
  }

  setSeries(series) { this.series = series; this.draw(); }
  setData(points) { this.points = points || []; this.draw(); }

  _colors() {
    const css = getComputedStyle(document.documentElement);
    return {
      grid: css.getPropertyValue("--border").trim() || "#223352",
      text: css.getPropertyValue("--text-faint").trim() || "#5c7194",
      bg: css.getPropertyValue("--panel").trim() || "#111b30",
    };
  }

  _bounds() {
    let max = this.minY;
    for (const p of this.points) {
      for (const s of this.series) max = Math.max(max, +(p[s.key] || 0));
    }
    return max * 1.2 || 1;
  }

  _onMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const n = this.points.length;
    if (!n) return;
    const pad = { l: 44, r: 8, t: 10, b: 18 };
    const w = rect.width - pad.l - pad.r;
    const idx = Math.round(((x - pad.l) / w) * (n - 1));
    this.hoverIdx = Math.max(0, Math.min(n - 1, idx));
    this.draw(e.clientY - rect.top);
  }

  draw(hoverY = null) {
    const canvas = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width) return;
    canvas.width = rect.width * dpr;
    canvas.height = this.height * dpr;
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width, H = this.height;
    const colors = this._colors();
    const pad = { l: 44, r: 8, t: 10, b: 18 };
    ctx.clearRect(0, 0, W, H);

    const n = this.points.length;
    const max = this._bounds();
    // grid
    ctx.strokeStyle = colors.grid;
    ctx.fillStyle = colors.text;
    ctx.font = "10px Consolas, monospace";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + ((H - pad.t - pad.b) * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(W - pad.r, y);
      ctx.stroke();
      const label = this._fmt(max * (1 - i / 4));
      ctx.fillText(label, 4, y + 3);
    }
    if (!n) {
      ctx.fillStyle = colors.text;
      ctx.fillText("waiting for samples…", pad.l + 8, H / 2);
      return;
    }
    const xAt = (i) => pad.l + ((W - pad.l - pad.r) * i) / Math.max(1, n - 1);
    const yAt = (v) => pad.t + (H - pad.t - pad.b) * (1 - Math.min(1, v / max));

    for (const s of this.series) {
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const x = xAt(i), y = yAt(+(this.points[i][s.key] || 0));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 1.6;
      ctx.stroke();
      if (s.fill) {
        ctx.lineTo(xAt(n - 1), H - pad.b);
        ctx.lineTo(xAt(0), H - pad.b);
        ctx.closePath();
        const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
        grad.addColorStop(0, s.color + "44");
        grad.addColorStop(1, s.color + "00");
        ctx.fillStyle = grad;
        ctx.fill();
      }
    }

    // hover crosshair + tooltip
    if (this.hoverIdx >= 0 && n) {
      const x = xAt(this.hoverIdx);
      ctx.strokeStyle = colors.text;
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, H - pad.b);
      ctx.stroke();
      const p = this.points[this.hoverIdx];
      const lines = this.series.map((s) => `${s.label}: ${this._fmt(p[s.key] || 0)}`);
      const ts = p.ts ? new Date((p.ts > 1e12 ? p.ts : p.ts * 1000)).toLocaleTimeString() : "";
      const boxW = 150, boxH = 16 + lines.length * 13;
      let bx = x + 8;
      if (bx + boxW > W) bx = x - boxW - 8;
      let by = pad.t + 6;
      ctx.fillStyle = colors.bg;
      ctx.strokeStyle = colors.grid;
      ctx.fillRect(bx, by, boxW, boxH);
      ctx.strokeRect(bx, by, boxW, boxH);
      ctx.fillStyle = colors.text;
      ctx.fillText(ts, bx + 6, by + 13);
      lines.forEach((ln, i) => ctx.fillText(ln, bx + 6, by + 26 + i * 13));
    }
  }

  _fmt(v) {
    if (this.unit === "bps") {
      if (v >= 1e9) return (v / 1e9).toFixed(1) + " G";
      if (v >= 1e6) return (v / 1e6).toFixed(1) + " M";
      if (v >= 1e3) return (v / 1e3).toFixed(1) + " k";
      return v.toFixed(0);
    }
    return v >= 100 ? v.toFixed(0) : v.toFixed(1);
  }
}

export function sparkline(canvas, values, color = "#22d3ee") {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !values.length) return;
  canvas.width = rect.width * dpr;
  canvas.height = 34 * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const max = Math.max(...values, 1);
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = (rect.width * i) / (values.length - 1 || 1);
    const y = 32 - (32 * v) / max;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.stroke();
}
