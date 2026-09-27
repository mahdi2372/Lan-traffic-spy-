/* Reports page: generate & download HTML / JSON / CSV / PDF reports. */

import { el, mount, toast, loadingState } from "../ui.js";

const FORMATS = [
  ["html", "HTML report", "Styled document — network summary, inventory, history, alerts, traffic"],
  ["json", "JSON export", "Machine-readable full dataset for tooling"],
  ["csv", "CSV inventory", "Spreadsheet-friendly device inventory + alerts"],
  ["pdf", "PDF report", "Printable report for records and sharing"],
];

const RANGES = [["today", "Today"], ["24h", "Last 24 hours"], ["7d", "Last 7 days"], ["30d", "Last 30 days"], ["all", "Everything"]];

export async function render(container, ctx) {
  const { api } = ctx;
  let preset = "7d";

  const rangeSel = el("select", { "aria-label": "Report time range" },
    ...RANGES.map(([v, l]) => el("option", { value: v, selected: v === preset }, l)));
  rangeSel.addEventListener("change", () => { preset = rangeSel.value; });

  const cards = el("div", { class: "grid cols-2" });
  for (const [fmtName, title, desc] of FORMATS) {
    cards.append(el("div", { class: "panel" },
      el("h2", {}, title),
      el("p", { class: "dim", style: "margin:0 0 10px" }, desc),
      el("button", {
        class: "btn primary",
        onclick: async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true;
          btn.textContent = "Generating…";
          try {
            const name = await api.download(fmtName, preset);
            toast(`Downloaded ${name}`, "ok");
          } catch (err) {
            toast(`${err.what || "Report failed"} — ${err.fix || ""}`, "danger", 5500);
          }
          btn.disabled = false;
          btn.textContent = `⇩ Download ${fmtName.toUpperCase()}`;
        },
      }, `⇩ Download ${fmtName.toUpperCase()}`),
    ));
  }

  mount(container,
    el("div", { class: "row", style: "margin-bottom:12px" },
      el("label", {}, "Time range ", rangeSel),
      el("span", { class: "faint" }, "Reports include: network summary · device inventory · Wi-Fi / Ethernet / unknown devices · history · health · alerts · traffic statistics"),
    ),
    cards,
    el("div", { class: "panel", style: "margin-top:14px" },
      el("h2", {}, "Privacy note"),
      el("p", { class: "dim", style: "margin:0" },
        "Reports contain metadata only (addresses, names, vendors, timestamps, counters). ",
        "No packet payloads, credentials or personal content can appear in a report — the application never collects them.")),
  );
}
