/* SVG network topology renderer.

Renders only relationships the backend derived from evidence:
Internet -> Gateway -> segments/APs -> clients. Node colors distinguish
router / AP / wired / wireless / unknown. Clicking a client opens its profile.
*/

const COLORS = {
  internet: "#a78bfa",
  router: "#22d3ee",
  ap: "#34d399",
  segment: "#5c7194",
  "client-wifi": "#22d3ee",
  "client-ethernet": "#a78bfa",
  "client-unknown": "#8fa3c1",
  self: "#fbbf24",
};

export function renderTopology(svg, topo, { onNodeClick } = {}) {
  const NS = "http://www.w3.org/2000/svg";
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const width = svg.clientWidth || 800;
  const height = 460;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const nodes = topo.nodes || [];
  const edges = topo.edges || [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const children = new Map();
  for (const e of edges) {
    if (!children.has(e.from)) children.set(e.from, []);
    children.get(e.from).push(e.to);
  }

  // layout: tier rows
  const tiers = [0, 1, 2, 3];
  const rows = tiers.map((t) => nodes.filter((n) => n.tier === t));
  const pos = new Map();
  rows.forEach((row, ti) => {
    const y = 50 + ti * 120;
    row.forEach((n, i) => {
      const x = ((i + 1) * width) / (row.length + 1);
      pos.set(n.id, { x, y });
    });
  });

  // edges first
  for (const e of edges) {
    const a = pos.get(e.from), b = pos.get(e.to);
    if (!a || !b) continue;
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y + 12);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y - 14);
    line.setAttribute("stroke", e.kind === "wan" ? "#a78bfa" : "#2c4066");
    line.setAttribute("stroke-width", e.kind === "wan" ? 2 : 1.3);
    if (e.kind === "wan") line.setAttribute("stroke-dasharray", "5 4");
    svg.append(line);
  }

  for (const n of nodes) {
    const p = pos.get(n.id);
    if (!p) continue;
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "topo-node");
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.setAttribute("aria-label", `${n.kind}: ${n.label}`);
    if (onNodeClick && n.device_id) {
      g.addEventListener("click", () => onNodeClick(n));
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") onNodeClick(n); });
    }

    const glow = document.createElementNS(NS, "circle");
    glow.setAttribute("class", "topo-glow");
    glow.setAttribute("cx", p.x); glow.setAttribute("cy", p.y);
    glow.setAttribute("r", 20);
    glow.setAttribute("fill", COLORS[n.kind] || "#8fa3c1");
    glow.setAttribute("opacity", n.online === false ? 0.08 : 0.22);
    g.append(glow);

    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", p.x); c.setAttribute("cy", p.y);
    c.setAttribute("r", n.kind === "internet" || n.kind === "router" ? 13 : 10);
    c.setAttribute("fill", "#0d1526");
    c.setAttribute("stroke", n.online === false ? "#5c7194" : (COLORS[n.kind] || "#8fa3c1"));
    c.setAttribute("stroke-width", 2.2);
    if (n.online === false) c.setAttribute("stroke-dasharray", "3 3");
    g.append(c);

    const core = document.createElementNS(NS, "circle");
    core.setAttribute("cx", p.x); core.setAttribute("cy", p.y);
    core.setAttribute("r", 4);
    core.setAttribute("fill", n.online === false ? "#5c7194" : (COLORS[n.kind] || "#8fa3c1"));
    g.append(core);

    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "topo-label");
    label.setAttribute("x", p.x); label.setAttribute("y", p.y + 26);
    label.setAttribute("text-anchor", "middle");
    label.textContent = (n.label || "").slice(0, 22);
    g.append(label);

    if (n.ip) {
      const sub = document.createElementNS(NS, "text");
      sub.setAttribute("class", "topo-sub");
      sub.setAttribute("x", p.x); sub.setAttribute("y", p.y + 38);
      sub.setAttribute("text-anchor", "middle");
      sub.textContent = n.ip;
      g.append(sub);
    }
    svg.append(g);
  }

  // legend
  const legend = document.createElementNS(NS, "text");
  legend.setAttribute("x", 12); legend.setAttribute("y", height - 10);
  legend.setAttribute("class", "topo-sub");
  legend.textContent = "● router  ● AP  ● wired  ● Wi-Fi  ● unknown  |  dashed = WAN  |  relationships shown only where evidence exists";
  svg.append(legend);
}
