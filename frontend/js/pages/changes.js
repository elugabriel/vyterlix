// What changed: the movements in the business's figures that are bigger than normal, newest month
// first, with what moved, by how much, and whether it is good or bad news. Everyone in the
// business can look. A change the owner's own busy and quiet seasons lead them to expect is shown
// but marked as expected.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { acceptForm, loadPeople } from "../actions.js";
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
let me = null;
let people = [];

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

async function start({ user, org }) {
  orgId = org.id;
  me = user;
  if (org.role === "owner" || org.role === "manager") people = await loadPeople(org.id, user);
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
    el("p", { class: "muted" }, "Figures that moved by more than usual. Small ups and downs are left out."),
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
  slot.replaceChildren(summaryBar(events), ...[...months].map(([month, figures]) => monthCard(month, [...figures.values()])));
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

/** How many changes are good news and how many are worth a look, as one bar. */
function summaryBar(events) {
  const latest = events.reduce((a, e) => (e.period_start > a ? e.period_start : a), "");
  const one = events.filter((e) => e.kind === "material_change" && e.period_start === latest);
  const counts = [
    [one.filter((e) => e.effect === "bad").length, "glance-bad-bar", "worth a look"],
    [one.filter((e) => e.effect === "neutral").length, "glance-grey", "neutral"],
    [one.filter((e) => e.effect === "good").length, "glance-ok", "good news"],
  ].filter(([n]) => n > 0);
  if (!counts.length) return el("span");
  return el(
    "section",
    { class: "card outcome-bar-box" },
    el("div", { class: "outcome-bar", role: "img", "aria-label": counts.map(([n, , w]) => `${n} ${w}`).join(", ") }, ...counts.map(([n, c]) => el("span", { class: c, style: `flex:${n}` }))),
    el("p", { class: "muted" }, `${periodLabel(latest, "month")}: ${counts.map(([n, , w]) => `${n} ${w}`).join(" · ")}`),
  );
}

/** Last month and this month as two bars of the same scale, so the size of the move can be seen at a glance. */
function beforeAfter(event) {
  const before = Number(event.reference_value);
  const now = Number(event.value);
  const top = Math.max(Math.abs(before), Math.abs(now)) || 1;
  const row = (label, n, text, cls) =>
    el(
      "div",
      { class: "ba-row" },
      el("span", { class: "ba-label muted" }, label),
      el("span", { class: "share-track", "aria-hidden": "true" }, el("span", { class: `share-fill ${cls}`, style: `width:${Math.max(2, (Math.abs(n) / top) * 100)}%` })),
      el("span", { class: "ba-value" }, text),
    );
  return el("div", { class: "ba" }, row("Last month", before, formatValue(event.reference_value, event.unit), "ba-before"), row("This month", now, formatValue(event.value, event.unit), event.effect === "good" ? "ba-good" : event.effect === "bad" ? "share-over" : "ba-before"));
}

function eventRow({ change, unusual }) {
  const event = change ?? unusual;
  const words = event.plain;
  const size = `${event.direction === "up" ? "+" : "-"}${Math.abs(Number(event.change)).toFixed(1)}${event.change_unit === "points" ? " pts" : "%"}`;
  const more = el(
    "details",
    {},
    el("summary", {}, "The details"),
    words ? el("p", { class: "muted" }, words.numbers) : null,
    words ? el("p", {}, words.why) : null,
    change && unusual && unusual.plain ? el("p", { class: "muted" }, `${unusual.plain.numbers} That is further from normal than it usually strays.`) : null,
    event.explained_by_season && !unusual ? el("p", { class: "muted" }, "Expected for the time of year, so probably nothing to worry about.") : null,
    words && words.data_note ? el("p", { class: "status-bad" }, words.data_note) : null,
  );
  return el(
    "div",
    { class: `kpi change-row change-${event.effect}` },
    el(
      "div",
      { class: "change-head" },
      el("strong", {}, event.kpi_name),
      el("span", { class: `change-size ${EFFECT_CLASS[event.effect]}` }, `${ARROW[event.direction]} ${size}`),
    ),
    el("div", {}, el("span", { class: `badge ${EFFECT_CLASS[event.effect]}` }, EFFECT_TEXT[event.effect]), " ", event.severity === "major" ? el("span", { class: "muted" }, SEVERITY_TEXT.major) : null, unusual && change ? el("span", { class: "badge" }, "Also unusual for your business") : null),
    beforeAfter(event),
    el("p", { class: "plain-says" }, words ? words.happened : event.summary),
    el("div", { class: "change-more" }, more, diagnosisPanel(event), event.effect === "bad" ? recommendationPanel(event) : null, splittable[event.kpi_code] ? whereFrom(event) : null),
    el("a", { href: `change.html?org=${orgId}&event=${event.id}` }, "See the whole story"),
    " · ",
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
  dismissed: "You decided this is not for you.",
  accepted: "You have taken this up. Follow it on the Actions page.",
  proposed: "This has been suggested as an action and is waiting for the owner to approve it.",
};

function recommendationPanel(event) {
  const result = el("div", { class: "stack" });
  const panel = el("details", { class: "recommendation" }, el("summary", {}, "What should I do about it?"), result);
  const show = (recommendation) => result.replaceChildren(recommendationView(recommendation, make, event, show));
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

function decide(recommendation, event, show) {
  const area = el("div", { class: "stack" });
  const finish = async (action) => {
    if (action) {
      area.replaceChildren(
        el("p", { class: "status-ok" }, action.status === "pending" ? "Suggested. The owner needs to approve it before it starts." : "Accepted. It is now on your Actions page."),
        el("a", { href: `actions.html?org=${orgId}#${action.id}` }, "Open the action"),
      );
    }
  };
  const choose = (option) =>
    area.replaceChildren(
      acceptForm(option, people, me, {
        onCancel: () => area.replaceChildren(buttons),
        onAccept: async (body) => finish(await guard(message, () => api.post(`${base}/${event.id}/recommendation/accept`, body))),
      }),
    );
  const chooser = select("option", recommendation.options.map((o) => [o.id, `${o.rank}. ${o.title}`]), { selected: recommendation.options[0].id });
  const buttons = el(
    "div",
    { class: "actions" },
    el("button", { type: "button", onclick: () => choose(recommendation.options.find((o) => o.id === chooser.value)) }, "Accept and set it up"),
    chooser,
    el("button", { type: "button", class: "secondary", onclick: async () => {
      const done = await guard(message, async () => (await api.post(`${base}/${event.id}/recommendation/dismiss`, {}), true));
      if (done) show({ ...recommendation, status: "dismissed" });
    } }, "Not for me"),
  );
  area.append(buttons);
  return area;
}

const PRIORITY_CLASS = { high: "health-at_risk", medium: "health-fair", low: "health-not_enough_data" };

function recommendationView(recommendation, again, event, show) {
  const [best, ...others] = recommendation.options;
  const groups = EVIDENCE_GROUPS.map(([type, title]) => [title, recommendation.evidence.filter((e) => e.evidence_type === type)]).filter(([, items]) => items.length);
  return el(
    "div",
    { class: "stack" },
    el("p", {}, el("strong", {}, recommendation.headline)),
    recommendation.priority ? el("p", {}, el("span", { class: `badge ${PRIORITY_CLASS[recommendation.priority.level]}` }, `${recommendation.priority.label} priority`), " ", recommendation.priority.reason) : null,
    RECOMMENDATION_NOTICE[recommendation.status] ? el("p", { class: "muted" }, RECOMMENDATION_NOTICE[recommendation.status]) : null,
    best
      ? el(
          "div",
          { class: "stack" },
          el("div", { class: "kpi" }, el("div", { class: "kpi-name" }, `Do this first: ${best.title}`), el("p", {}, best.description), badges(best), el("strong", {}, "How to do it"), el("ol", {}, ...best.intervention.steps.map((step) => el("li", {}, step)))),
          el("p", {}, el("strong", {}, "Why this one. "), recommendation.rationale),
        )
      : el("p", {}, recommendation.rationale),
    best && recommendation.status === "open" && canExplain ? decide(recommendation, event, show) : null,
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
