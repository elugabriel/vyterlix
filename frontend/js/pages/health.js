// Business health: one score for how the business is doing, why, and the evidence behind it.
// Everyone in the business can look. Each area opens up to show the exact figures it judged.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put, table } from "../data.js";
import { el } from "../dom.js";
import { field, select } from "../forms.js";
import { ukDateTime } from "../format.js";
import { barChart, formatValue, periodLabel } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const STATUS_TEXT = { healthy: "Healthy", fair: "Fair", needs_attention: "Needs attention", at_risk: "At risk", not_enough_data: "Not enough data" };
const ARROW = { up: "▲", down: "▼", flat: "•" };

let orgId = null;
let base = "";

async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${orgId}/business-health`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;
  const [latest, history] = await Promise.all([guard(message, () => api.get(base)), guard(message, () => api.get(`${base}/history?limit=24`))]);
  if (!latest || !history) return;
  if (!history.points.length) {
    content.replaceChildren(emptyCard());
    return;
  }
  const detail = el("div", { class: "stack" });
  content.replaceChildren(monthPicker(history, latest, detail), detail, historyCard(history));
  showMonth(detail, latest);
}

function emptyCard() {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "No health score yet"),
    el("p", {}, "Your health score is worked out from your key figures. Add some sales and costs (or upload a file), then work out your figures."),
    el("div", { class: "actions" }, el("a", { class: "button", href: `kpis.html?org=${orgId}` }, "Go to key figures")),
  );
}

// --- the picture for one month --------------------------------------------------------------------------

function trendLine(trend, score, previous, unit = "points") {
  if (!trend || previous === null) return null;
  const change = score - previous;
  const word = trend === "flat" ? "about the same as" : `${trend === "up" ? "up" : "down"} ${Math.abs(change)} ${unit} on`;
  return el("span", { class: trend === "up" ? "status-ok" : trend === "down" ? "status-bad" : "muted" }, `${ARROW[trend]} ${word} the month before`);
}

function showMonth(slot, health) {
  const scored = health.components.filter((c) => c.score !== null);
  const totalWeight = scored.reduce((sum, c) => sum + c.weight, 0);
  slot.replaceChildren(
    heroCard(health),
    el("div", { class: "kpi-grid" }, ...health.components.map((c) => areaCard(c, totalWeight))),
  );
}

function heroCard(health) {
  const month = periodLabel(health.period_start, "month");
  const card = el("section", { class: "card" });
  put(
    card,
    el("h2", { style: "margin-top:0" }, `Your business health in ${month}`),
    health.overall_score === null
      ? el("p", { class: "kpi-value muted" }, "–")
      : el(
          "div",
          { class: "score-hero" },
          el("div", {}, el("span", { class: `number health-${health.status}` }, String(health.overall_score)), el("span", { class: "muted" }, " out of 100")),
          el("div", { style: "flex:1;min-width:220px" }, el("span", { class: `badge health-${health.status}` }, STATUS_TEXT[health.status]), " ", trendLine(health.trend, health.overall_score, health.previous_score)),
        ),
    el("p", {}, health.explanation),
    health.data_quality !== null && health.data_quality < 80
      ? el("p", { class: "status-bad" }, `Some of the data behind this is incomplete (${health.data_quality} out of 100), so treat it with care. See Data overview for what to fix.`)
      : null,
    el("p", { class: "muted" }, `Worked out on ${ukDateTime(health.calculated_at)}. `, el("a", { href: `kpis.html?org=${orgId}` }, "See the key figures")),
  );
  return card;
}

function areaCard(c, totalWeight) {
  const tile = el("div", { class: "kpi" });
  put(tile, el("div", { class: "kpi-name" }, c.label));
  if (c.score === null) {
    put(tile, el("div", { class: "kpi-value muted" }, "–"), el("div", { class: "muted" }, c.explanation));
    return tile;
  }
  const share = totalWeight ? Math.round((c.weight / totalWeight) * 100) : 0;
  put(
    tile,
    el("div", {}, el("span", { class: `kpi-value health-${c.status}` }, String(c.score)), el("span", { class: "muted" }, " / 100 "), el("span", { class: `badge health-${c.status}` }, STATUS_TEXT[c.status])),
    el("progress", { max: 100, value: c.score, "aria-label": `${c.label} score`, style: "width:100%" }),
    el("div", {}, trendLine(c.trend, c.score, c.previous_score)),
    el("div", { class: "muted" }, `Counts for ${share}% of the score`),
    el("p", {}, c.explanation),
    evidence(c),
  );
  return tile;
}

function evidence(c) {
  return el(
    "details",
    {},
    el("summary", {}, "The figures behind this score"),
    table(
      ["Figure", "This month", "Score"],
      c.metrics.map((m) => [
        el("a", { href: `kpis.html?org=${orgId}#${m.kpi_code}` }, m.name),
        formatValue(m.value, m.unit),
        `${Math.round(m.score)}/100`,
      ]),
      { empty: "Nothing could be measured." },
    ),
    ...c.metrics.map((m) => el("p", { class: "muted" }, m.text)),
  );
}

// --- choosing a month, and the history ------------------------------------------------------------------------

function monthPicker(history, latest, slot) {
  const months = [...history.points].reverse();
  const chooser = select("month", months.map((p) => [p.period_start, periodLabel(p.period_start, "month")]), { selected: latest.period_start });
  chooser.addEventListener("change", async () => {
    const health = await guard(message, () => api.get(`${base}/${chooser.value}`));
    if (health) showMonth(slot, health);
  });
  return el("section", { class: "card" }, field("Show the month of", chooser));
}

function historyCard(history) {
  const values = history.points.map((p) => ({
    period_start: p.period_start,
    is_complete: true,
    status: p.overall_score === null ? "no_data" : "ok",
    value: p.overall_score === null ? null : String(p.overall_score),
  }));
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Month by month"),
    barChart(values, "count", "Business health score by month"),
    table(
      ["Month", "Score", "How it's going"],
      [...history.points].reverse().map((p) => [
        periodLabel(p.period_start, "month"),
        p.overall_score === null ? "–" : `${p.overall_score}/100`,
        el("span", {}, el("span", { class: `badge health-${p.status}` }, STATUS_TEXT[p.status]), " ", p.trend ? el("span", { class: p.trend === "up" ? "status-ok" : p.trend === "down" ? "status-bad" : "muted" }, ARROW[p.trend]) : null),
      ]),
    ),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
