// What changed: the movements in the business's figures that are bigger than normal, newest month
// first, with what moved, by how much, and whether it is good or bad news. Everyone in the
// business can look. A change the owner's own busy and quiet seasons lead them to expect is shown
// but marked as expected.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, table } from "../data.js";
import { el } from "../dom.js";
import { field, select } from "../forms.js";
import { ukDateTime } from "../format.js";
import { formatValue, periodLabel } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const EFFECT_TEXT = { good: "Good news", bad: "Worth a look", neutral: "A change" };
const EFFECT_CLASS = { good: "status-ok", bad: "status-bad", neutral: "muted" };
const SEVERITY_TEXT = { major: "Big change", notable: "Noticeable change" };
const ARROW = { up: "▲", down: "▼" };
const DIMENSION_TEXT = {
  product: "Product",
  channel: "Where it was sold",
  customer: "Customer",
  weekday: "Day of the week",
  category: "Type of cost",
  supplier: "Supplier",
};

let orgId = null;
let base = "";
let segmentBase = "";
let driversBase = "";
let splittable = {};
let canExplain = false;

const CONFIDENCE_TEXT = { high: "High confidence", medium: "Medium confidence", low: "Low confidence", insufficient: "Not enough evidence" };
const CONFIDENCE_CLASS = { high: "health-healthy", medium: "health-fair", low: "health-needs_attention", insufficient: "health-not_enough_data" };
// What each kind of statement is called on screen, in the order they are shown. A guess is never
// shown as a fact: every line says what kind of statement it is.
const EVIDENCE_GROUPS = [
  ["fact", "What your records show"],
  ["statistical", "What we worked out from your figures"],
  ["ai_interpretation", "What the AI assistant thinks"],
  ["insufficient", "What we can't tell"],
];

async function start({ org }) {
  orgId = org.id;
  canExplain = org.role === "owner" || org.role === "manager";
  base = `/organizations/${orgId}/changes`;
  segmentBase = `/organizations/${orgId}/segments`;
  driversBase = `/organizations/${orgId}/drivers`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;

  const options = await guard(message, () => api.get(segmentBase));
  splittable = options ? options.metrics : {};
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
    diagnosisPanel(event),
    event.effect === "bad" ? recommendationPanel(event) : null,
    splittable[event.kpi_code] ? whereFrom(event) : null,
    el("a", { href: `kpis.html?org=${orgId}#${event.kpi_code}` }, "See this figure"),
  );
}

// --- why did it happen? the diagnosis and the evidence behind it -------------------------------------------

function diagnosisPanel(event) {
  const result = el("div", { class: "stack" });
  const panel = el("details", { class: "diagnosis" }, el("summary", {}, "Why did this happen?"), result);
  let diagnosed = event.status === "diagnosed";

  const show = (diagnosis) => {
    diagnosed = true;
    result.replaceChildren(diagnosisView(diagnosis, explain));
  };
  async function explain() {
    const diagnosis = await guard(message, () => api.post(`${base}/${event.id}/diagnosis`));
    if (diagnosis) show(diagnosis);
  }
  const notYet = () =>
    result.replaceChildren(
      canExplain
        ? el(
            "div",
            { class: "stack" },
            el("p", {}, "This change has not been explained yet."),
            el("div", { class: "actions" }, el("button", { type: "button", onclick: explain }, "Explain this")),
          )
        : el("p", { class: "muted" }, "This change has not been explained yet. The owner or a manager can ask for an explanation."),
    );

  let opened = false;
  panel.addEventListener("toggle", async () => {
    if (!panel.open || opened) return;
    opened = true;
    if (!diagnosed) return notYet();
    const diagnosis = await guard(message, () => api.get(`${base}/${event.id}/diagnosis`));
    if (diagnosis) show(diagnosis);
  });
  return panel;
}

function diagnosisView(diagnosis, again) {
  const groups = EVIDENCE_GROUPS.map(([type, title]) => [title, diagnosis.evidence.filter((e) => e.evidence_type === type)]).filter(([, items]) => items.length);
  return el(
    "div",
    { class: "stack" },
    el("p", {}, el("strong", {}, diagnosis.headline)),
    el(
      "p",
      {},
      el("span", { class: `badge ${CONFIDENCE_CLASS[diagnosis.confidence_label]}` }, CONFIDENCE_TEXT[diagnosis.confidence_label]),
      diagnosis.confidence === null ? null : el("span", { class: "muted" }, ` ${diagnosis.confidence} out of 100`),
    ),
    diagnosis.confidence_note ? el("p", { class: "muted" }, diagnosis.confidence_note) : null,
    ...groups.map(([title, items]) =>
      el("div", {}, el("strong", {}, title), el("ul", {}, ...items.map((e) => el("li", {}, e.statement)))),
    ),
    el("p", { class: "muted" }, `Worked out on ${ukDateTime(diagnosis.diagnosed_at)} using version ${diagnosis.rules_version} of the rules. Every line above comes from your records or from arithmetic on them; nothing here is a guess.`),
    canExplain ? el("div", { class: "actions" }, el("button", { type: "button", class: "secondary", onclick: again }, "Work it out again")) : null,
  );
}

// --- what should I do? the options, ranked, and why one is recommended -------------------------------

const EFFORT_TEXT = { low: "Low effort", medium: "Moderate effort", high: "A lot of effort" };
const COST_TEXT = { none: "No cost", low: "Low cost", medium: "Moderate cost", high: "High cost" };
const RECOMMENDATION_NOTICE = {
  no_action_needed: "This is good news, so there is nothing to put right.",
  insufficient_evidence: "We cannot say what to do yet, because we cannot say why this happened.",
};

function recommendationPanel(event) {
  const result = el("div", { class: "stack" });
  const panel = el("details", { class: "recommendation" }, el("summary", {}, "What should I do about it?"), result);
  const show = (recommendation) => result.replaceChildren(recommendationView(recommendation, make));
  async function make() {
    const recommendation = await guard(message, () => api.post(`${base}/${event.id}/recommendation`));
    if (recommendation) show(recommendation);
  }
  const notYet = () =>
    result.replaceChildren(
      canExplain
        ? el(
            "div",
            { class: "stack" },
            el("p", {}, "We have not worked out what to do about this yet."),
            el("div", { class: "actions" }, el("button", { type: "button", onclick: make }, "Suggest what to do")),
          )
        : el("p", { class: "muted" }, "Nothing has been suggested for this yet. The owner or a manager can ask for suggestions."),
    );
  let opened = false;
  panel.addEventListener("toggle", async () => {
    if (!panel.open || opened) return;
    opened = true;
    try {
      show(await api.get(`${base}/${event.id}/recommendation`));
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) notYet();
      else throw error;
    }
  });
  return panel;
}

function badges(option) {
  return el(
    "p",
    {},
    el("span", { class: "badge plain" }, EFFORT_TEXT[option.effort]),
    " ",
    el("span", { class: "badge plain" }, COST_TEXT[option.cost_level]),
    " ",
    el("span", { class: "badge plain" }, `Starts to show in about ${option.days_to_effect} days`),
  );
}

function scoreTable(option) {
  return table(
    ["What we looked at", "Score", "How much it counts"],
    option.scores.map((line) => [line.label, `${line.score} out of 100`, `${line.weight}%`]),
  );
}

function recommendationView(recommendation, again) {
  const [best, ...others] = recommendation.options;
  const groups = EVIDENCE_GROUPS.map(([type, title]) => [title, recommendation.evidence.filter((e) => e.evidence_type === type)]).filter(([, items]) => items.length);
  return el(
    "div",
    { class: "stack" },
    el("p", {}, el("strong", {}, recommendation.headline)),
    RECOMMENDATION_NOTICE[recommendation.status] ? el("p", { class: "muted" }, RECOMMENDATION_NOTICE[recommendation.status]) : null,
    best
      ? el(
          "div",
          { class: "stack" },
          el("div", { class: "kpi" }, el("div", { class: "kpi-name" }, `Do this first: ${best.title}`), el("p", {}, best.description), badges(best), el("strong", {}, "How to do it"), el("ol", {}, ...best.intervention.steps.map((step) => el("li", {}, step)))),
          el("p", {}, el("strong", {}, "Why this one. "), recommendation.rationale),
        )
      : el("p", {}, recommendation.rationale),
    best ? el("details", {}, el("summary", {}, `How it scored (${best.total_score} out of 100)`), scoreTable(best)) : null,
    others.length
      ? el(
          "div",
          { class: "stack" },
          el("strong", {}, "Other options we weighed"),
          ...others.map((option) =>
            el("details", {}, el("summary", {}, `${option.rank}. ${option.title} (${option.total_score} out of 100)`), el("p", {}, option.description), badges(option), scoreTable(option)),
          ),
        )
      : null,
    ...groups.map(([title, items]) => el("div", {}, el("strong", {}, title), el("ul", {}, ...items.map((e) => el("li", {}, e.statement))))),
    el("p", { class: "muted" }, `Worked out on ${ukDateTime(recommendation.generated_at)} using version ${recommendation.rules_version} of the rules. The order comes from rules and the figures above, not from a guess. The amounts are starting estimates, not promises.`),
    canExplain ? el("div", { class: "actions" }, el("button", { type: "button", class: "secondary", onclick: again }, "Work it out again")) : null,
  );
}

// --- where did it come from? the figure split into its parts ---------------------------------------------

function whereFrom(event) {
  const dimensions = splittable[event.kpi_code];
  const chooser = select("dimension", dimensions.map((d) => [d, DIMENSION_TEXT[d] ?? d]), { selected: dimensions[0] });
  const result = el("div", { class: "stack" });
  const why = el("div", { class: "stack" });
  const loadWhy = async () => {
    const params = new URLSearchParams({ month: event.period_start });
    const body = await guard(message, () => api.get(`${driversBase}/${event.kpi_code}?${params}`));
    if (body) why.replaceChildren(driversList(body));
  };
  const load = async () => {
    const params = new URLSearchParams({ month: event.period_start });
    const body = await guard(message, () => api.get(`${segmentBase}/${event.kpi_code}/${chooser.value}?${params}`));
    if (body) result.replaceChildren(partsTable(body));
  };
  chooser.addEventListener("change", load);
  const panel = el(
    "details",
    {},
    el("summary", {}, "Where did this come from?"),
    el("div", { class: "stack" }, why, field("Split by", chooser), result),
  );
  let loaded = false;
  panel.addEventListener("toggle", () => {
    if (panel.open && !loaded) {
      loaded = true;
      loadWhy();
      load();
    }
  });
  return panel;
}

function driversList(body) {
  if (!body.findings.length) return el("p", { class: "muted" }, "Nothing stands out as the main cause.");
  return el(
    "div",
    { class: "stack" },
    el("strong", {}, "What drove it"),
    el(
      "ul",
      {},
      ...body.findings.map((f) =>
        el("li", {}, f.text, f.share_pct === null || f.kind === "contributor" ? null : el("span", { class: "muted" }, ` (${Math.round(Number(f.share_pct))}% of the change)`)),
      ),
    ),
    el("p", { class: "muted" }, "These are different ways of reading the same change, so the percentages are not meant to add up to 100."),
  );
}

function signed(value, unit) {
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${formatValue(value, unit)}`;
}

function partsTable(body) {
  if (!body.rows.length) return el("p", { class: "muted" }, body.headline + " No single part moved.");
  return el(
    "div",
    { class: "stack" },
    el("p", {}, body.headline),
    table(
      ["Part", "Before", "Now", "Change", "Share of the change"],
      body.rows.map((r) => [
        r.label,
        formatValue(r.previous, body.unit),
        formatValue(r.current, body.unit),
        el("span", { class: Number(r.change) > 0 ? "status-ok" : "status-bad" }, signed(r.change, body.unit)),
        r.share_of_change_pct === null ? "–" : `${Math.round(Number(r.share_of_change_pct))}%`,
      ]),
    ),
    el("p", { class: "muted" }, "A share over 100% means other parts moved the other way. A negative share means that part went against the overall change."),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
