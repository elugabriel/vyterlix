// Forecast: what the business's sales are likely to be over the next few months, with a range around
// every month, how that was worked out, and how accurate past forecasts turned out to be.
// Everyone in the business can look; the owner and managers can ask for the forecast to be worked out
// again.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put, table } from "../data.js";
import { el } from "../dom.js";
import { field, select } from "../forms.js";
import { ukDateTime } from "../format.js";
import { formatValue, periodLabel, rangeChart } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const KPI = "revenue";

let orgId = null;
let base = "";
let canMake = false;
let horizon = "3";

async function start({ org }) {
  orgId = org.id;
  canMake = org.role === "owner" || org.role === "manager";
  base = `/organizations/${orgId}/forecasts`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;
  await load();
}

async function load() {
  const [forecast, accuracy] = await Promise.all([guard(message, () => api.get(`${base}/${KPI}`)), guard(message, () => api.get(`${base}/${KPI}/accuracy`))]);
  if (forecast === undefined || !accuracy) return;
  if (forecast === null) return void content.replaceChildren(emptyCard());
  content.replaceChildren(
    forecast.status === "ok" ? mainCard(forecast) : tooEarlyCard(forecast),
    ...(forecast.status === "ok" ? [methodCard(forecast)] : []),
    accuracyCard(accuracy),
  );
}

// --- making a forecast -----------------------------------------------------------------------------

function makeButton(label) {
  const chooser = select("horizon", [["3", "3 months"], ["6", "6 months"], ["12", "12 months"]], { selected: horizon });
  const button = el("button", { type: "button", class: "secondary" }, label);
  button.addEventListener("click", async () => {
    horizon = chooser.value;
    button.disabled = true;
    const done = await guard(message, () => api.post(`${base}/${KPI}?horizon=${horizon}`));
    button.disabled = false;
    if (done) await load();
  });
  return el("div", { class: "actions" }, field("Look ahead", chooser), button);
}

function emptyCard() {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "No forecast yet"),
    el("p", {}, "A forecast is worked out from your key figures. Add some sales (or upload a file), work out your key figures, and it will appear here."),
    el("div", { class: "actions" }, el("a", { class: "button", href: `kpis.html?org=${orgId}` }, "Go to key figures")),
    canMake ? makeButton("Make a forecast now") : null,
  );
}

function tooEarlyCard(forecast) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Not enough history yet"),
    el("p", {}, forecast.explanation),
    el("p", { class: "muted" }, "We would rather say so than guess. The forecast will appear by itself as more months finish."),
    canMake ? makeButton("Try again") : null,
  );
}

// --- the forecast ----------------------------------------------------------------------------------

function mainCard(forecast) {
  const unit = forecast.unit;
  const card = el("section", { class: "card" });
  put(
    card,
    el("h2", { style: "margin-top:0" }, `${forecast.kpi_name}: the next ${forecast.predictions.length} months`),
    el("p", {}, forecast.explanation),
    rangeChart(forecast.history, forecast.predictions, unit, `${forecast.kpi_name}: recent months and the forecast`),
    el(
      "div",
      { class: "key" },
      el("span", {}, "━ What happened"),
      el("span", {}, "╍ What we expect"),
      el("span", {}, `▒ The range it should land in, ${forecast.interval_level} times out of 100`),
      el("span", {}, "◯ What really happened, once a month has finished"),
    ),
    table(
      ["Month", "We expect", `Most likely between (${forecast.interval_level}%)`, "What happened"],
      forecast.predictions.map((p) => [
        periodLabel(p.period_start, "month"),
        formatValue(p.value, unit),
        `${formatValue(p.lower, unit)} to ${formatValue(p.upper, unit)}`,
        p.actual_value === null ? el("span", { class: "muted" }, "Not finished yet") : formatValue(p.actual_value, unit),
      ]),
    ),
    forecast.adjusted_for_seasons ? el("p", { class: "muted" }, "Your busy and quiet seasons have been allowed for, so a normal seasonal swing is not mistaken for growth or decline.") : null,
    el("p", { class: "muted" }, `Worked out on ${ukDateTime(forecast.calculated_at)} from ${forecast.history_months} finished months, up to ${periodLabel(forecast.as_of, "month")}. A forecast is an estimate, not a promise: the further ahead, the wider the range.`),
    canMake ? makeButton("Work it out again") : null,
  );
  return card;
}

function methodCard(forecast) {
  const unit = forecast.unit;
  const tried = forecast.evaluations.filter((e) => e.kind === "backtest");
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "How this was worked out"),
    el("p", {}, `We tried each method on your own last few months: cover up a month, forecast it from what came before, and see how close it came. The closest was "${forecast.method.name}": ${forecast.method.description.toLowerCase()}`),
    table(
      ["Method", "Usually missed by", "Months tried", ""],
      tried.map((e) => [
        e.method.name,
        e.typical_miss_pct === null ? formatValue(e.typical_miss, unit) : `${formatValue(e.typical_miss, unit)} (${Math.round(Number(e.typical_miss_pct))}%)`,
        String(e.months_tested),
        e.chosen ? el("span", { class: "badge health-healthy" }, "Used") : "",
      ]),
    ),
    el("p", { class: "muted" }, "The range around each month comes from how far these misses usually stray, so a business that is hard to predict gets a wider range, not a more confident one."),
  );
}

// --- how accurate past forecasts were --------------------------------------------------------------

function accuracyCard(accuracy) {
  const unit = accuracy.unit;
  const card = el("section", { class: "card" });
  put(
    card,
    el("h2", { style: "margin-top:0" }, "How accurate our past forecasts were"),
    el("p", {}, accuracy.headline),
    accuracy.verdict ? el("p", { class: accuracy.verdict.startsWith("The ranges are doing") ? "status-ok" : "status-bad" }, accuracy.verdict) : null,
    accuracy.by_months_ahead.length > 1
      ? table(
          ["Months ahead", "Checked", "Usually missed by", "Landed in the range"],
          accuracy.by_months_ahead.map((a) => [String(a.months_ahead), String(a.checked), a.typical_miss_pct === null ? "–" : `${Math.round(Number(a.typical_miss_pct))}%`, `${Math.round(Number(a.within_range_pct))}%`]),
        )
      : null,
    accuracy.rows.length
      ? el(
          "details",
          {},
          el("summary", {}, "Every month we have been able to check"),
          table(
            ["Month", "We said", "Range", "What happened", "In the range?"],
            accuracy.rows.map((r) => [
              periodLabel(r.period_start, "month"),
              formatValue(r.predicted, unit),
              `${formatValue(r.lower, unit)} to ${formatValue(r.upper, unit)}`,
              formatValue(r.actual, unit),
              r.within_range ? el("span", { class: "status-ok" }, "Yes") : el("span", { class: "status-bad" }, "No"),
            ]),
          ),
        )
      : null,
  );
  return card;
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
