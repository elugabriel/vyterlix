// What changed: the movements in the business's figures that are bigger than normal, newest month
// first, with what moved, by how much, and whether it is good or bad news. Everyone in the
// business can look. A change the owner's own busy and quiet seasons lead them to expect is shown
// but marked as expected.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard } from "../data.js";
import { el } from "../dom.js";
import { field, select } from "../forms.js";
import { periodLabel } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const EFFECT_TEXT = { good: "Good news", bad: "Worth a look", neutral: "A change" };
const EFFECT_CLASS = { good: "status-ok", bad: "status-bad", neutral: "muted" };
const SEVERITY_TEXT = { major: "Big change", notable: "Noticeable change" };
const ARROW = { up: "▲", down: "▼" };

let orgId = null;
let base = "";

async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${orgId}/changes`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;

  const results = el("div", { class: "stack" });
  const effect = select(
    "effect",
    [
      ["", "Everything"],
      ["bad", "Things worth a look"],
      ["good", "Good news"],
    ],
    { selected: "" },
  );
  const hideExpected = el("input", { type: "checkbox", id: "hide-expected" });
  const filters = el(
    "section",
    { class: "card" },
    field("Show", effect),
    el("label", { class: "check" }, hideExpected, " Hide changes my busy and quiet seasons explain"),
  );
  const refresh = () => show(results, effect.value, hideExpected.checked);
  effect.addEventListener("change", refresh);
  hideExpected.addEventListener("change", refresh);
  content.replaceChildren(
    el("p", { class: "muted" }, "Each figure is compared with the month before, and with how it usually behaves over the last year. Small ups and downs are left out; only the bigger movements appear."),
    filters,
    results,
  );
  await refresh();
}

async function show(slot, effect, hideExpected) {
  const params = new URLSearchParams({ limit: "200" });
  if (effect) params.set("effect", effect);
  if (hideExpected) params.set("include_expected", "false");
  const events = await guard(message, () => api.get(`${base}?${params}`));
  if (!events) return;
  if (!events.length) {
    slot.replaceChildren(emptyCard(effect || hideExpected));
    return;
  }
  // One card per figure per month. A figure can both have moved on the month before and be unusual
  // for the business, so the two are shown together, led by the move on the month before.
  const months = new Map();
  for (const event of events) {
    if (!months.has(event.period_start)) months.set(event.period_start, new Map());
    const figures = months.get(event.period_start);
    const entry = figures.get(event.kpi_code) ?? {};
    entry[event.kind === "anomaly" ? "unusual" : "change"] = event;
    figures.set(event.kpi_code, entry);
  }
  slot.replaceChildren(...[...months].map(([month, figures]) => monthCard(month, [...figures.values()])));
}

function emptyCard(filtered) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, filtered ? "Nothing matches" : "Nothing has changed by more than normal"),
    el(
      "p",
      {},
      filtered
        ? "Try showing everything."
        : "Once your key figures have been worked out for at least two months, big movements will appear here. If you have added data, work out your key figures first.",
    ),
    el("div", { class: "actions" }, el("a", { class: "button", href: `kpis.html?org=${orgId}` }, "Go to key figures")),
  );
}

function monthCard(month, events) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, periodLabel(month, "month")),
    el("div", { class: "stack" }, ...events.map(eventRow)),
  );
}

function eventRow({ change, unusual }) {
  const event = change ?? unusual;
  return el(
    "div",
    { class: "kpi" },
    el(
      "div",
      {},
      el("strong", {}, `${ARROW[event.direction]} ${event.kpi_name}`),
      " ",
      el("span", { class: `badge ${EFFECT_CLASS[event.effect]}` }, EFFECT_TEXT[event.effect]),
      " ",
      el("span", { class: "muted" }, SEVERITY_TEXT[event.severity]),
      unusual ? el("span", { class: "badge" }, "Unusual for you") : null,
    ),
    el("p", {}, event.summary),
    change && unusual ? el("p", {}, unusual.summary) : null,
    event.explained_by_season && !unusual ? el("p", { class: "muted" }, "Expected for the time of year, so probably nothing to worry about.") : null,
    event.data_quality !== null && event.data_quality < 80
      ? el("p", { class: "status-bad" }, `Some of the data behind this month is incomplete (${event.data_quality} out of 100), so treat it with care.`)
      : null,
    el("a", { href: `kpis.html?org=${orgId}#${event.kpi_code}` }, "See this figure"),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
