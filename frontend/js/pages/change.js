// One change on one page, from what happened to how it turned out (client feedback, sections 1 and 3).
// A strip shows the six steps and which one it is up to; under it, each step says what there is to
// know. Deciding uses the same card as the Alerts page, so there is one way to accept or dismiss.

import { mountCard } from "../alert-card.js";
import { api } from "../auth.js";
import { acceptForm, loadPeople } from "../actions.js";
import { openBusiness } from "../business.js";
import { guard, put } from "../data.js";
import { el } from "../dom.js";
import { ukDate } from "../format.js";
import { barChart, formatValue } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const STATE_CLASS = { done: "step-done", current: "step-current", todo: "step-todo", not_needed: "step-skipped" };
const STATE_MARK = { done: "✓", current: "●", todo: "", not_needed: "–" };
const CONFIDENCE = { high: "High confidence", medium: "Medium confidence", low: "Low confidence", insufficient: "Not enough evidence" };
const CONFIDENCE_CLASS = { high: "health-healthy", medium: "health-fair", low: "health-needs_attention", insufficient: "health-not_enough_data" };
const OUTCOME_CLASS = { successful: "health-healthy", partially_successful: "health-fair", unsuccessful: "health-at_risk", inconclusive: "health-not_enough_data" };

let orgId = null;
let eventId = null;
let base = "";
let me = null;
let people = [];

async function start({ user, org }) {
  orgId = org.id;
  me = user;
  if (org.role === "owner" || org.role === "manager") people = await loadPeople(org.id, user);
  eventId = new URLSearchParams(location.search).get("event") ?? "";
  base = `/organizations/${orgId}/changes/${eventId}`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `changes.html?org=${orgId}`;
  if (!eventId) {
    put(content, el("p", { class: "muted" }, "Choose a change from "), el("a", { href: `changes.html?org=${orgId}` }, "What changed"), el("span", { class: "muted" }, "."));
    return;
  }
  await render();
}

async function render() {
  const j = await guard(message, () => api.get(`${base}/journey`));
  if (!j) return;
  put(content.replaceChildren() ?? content, strip(j), happened(j), why(j), suggest(j), decide(j), doing(j), result(j));
  const waiting = ["open", "proposed"].includes(j.suggestion?.status);
  if (waiting && j.alert_id) {
    await mountCard(document.getElementById("decision-card"), { orgId, alertId: j.alert_id, pageLink: (p) => p, onChange: render });
  } else if (waiting && j.can_decide) {
    await inlineDecision(document.getElementById("decision-card"));
  }
}

/** A change with no alert has no card, so offer the same two choices here: set it up, or not for me. */
async function inlineDecision(area) {
  const rec = await guard(message, () => api.get(`${base}/recommendation`));
  if (!rec || !rec.options.length) return;
  const best = rec.options[0];
  const buttons = el(
    "div",
    { class: "actions" },
    el("button", { type: "button", onclick: () => area.replaceChildren(acceptForm(best, people, me, {
      onCancel: () => area.replaceChildren(buttons),
      onAccept: async (body) => { if (await guard(message, () => api.post(`${base}/recommendation/accept`, body))) await render(); },
    })) }, "Accept and set it up"),
    el("button", { type: "button", class: "secondary", onclick: async () => {
      if (await guard(message, async () => (await api.post(`${base}/recommendation/dismiss`, {}), true))) await render();
    } }, "Not for me"),
  );
  area.replaceChildren(buttons);
}

function strip(j) {
  return el(
    "ol",
    { class: "journey-steps" },
    ...j.steps.map((s, i) =>
      el(
        "li",
        { class: `journey-step ${STATE_CLASS[s.state]}` },
        el("span", { class: "journey-mark" }, STATE_MARK[s.state] || String(i + 1)),
        el("strong", {}, s.label),
        s.note ? el("span", { class: "muted" }, s.note) : null,
      ),
    ),
  );
}

function section(number, title, ...body) {
  return el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, `${number}. ${title}`), ...body);
}

function happened(j) {
  const words = j.event.plain;
  const chart = j.chart && j.chart.points.length ? barChart(j.chart.points.map((p) => ({ ...p, status: "ok" })), j.chart.unit, `${j.chart.name} over the last months`, { marked: j.chart.marked }) : null;
  return section(
    1,
    "What happened",
    el("h3", {}, words.headline),
    el("p", { class: `plain-says plain-${words.tone}` }, words.happened),
    el("p", { class: "muted" }, words.numbers),
    el("p", {}, words.why),
    chart,
    words.season ? el("p", { class: "muted" }, words.season) : null,
    words.data_note ? el("p", { class: "status-bad" }, words.data_note) : null,
  );
}

function why(j) {
  const d = j.diagnosis;
  if (!d) {
    const ask = j.can_decide ? el("button", { type: "button", onclick: async () => { if (await guard(message, () => api.post(`${base}/diagnosis`))) await render(); } }, "Work out why") : null;
    return section(2, "Why it happened", el("p", { class: "muted" }, "We have not looked into why yet."), ask);
  }
  return section(
    2,
    "Why it happened",
    el("p", {}, el("span", { class: `badge ${CONFIDENCE_CLASS[d.confidence_label]}` }, CONFIDENCE[d.confidence_label]), " ", d.headline),
    el("p", {}, d.summary),
    d.evidence.length ? el("details", {}, el("summary", {}, "What this is based on"), el("ul", {}, ...d.evidence.map((e) => el("li", {}, e)))) : null,
    d.cannot_tell.length ? el("details", {}, el("summary", {}, "What we cannot tell"), el("ul", {}, ...d.cannot_tell.map((e) => el("li", {}, e)))) : null,
  );
}

function suggest(j) {
  const s = j.suggestion;
  if (!s) {
    const ask = j.can_decide && j.diagnosis ? el("button", { type: "button", onclick: async () => { if (await guard(message, () => api.post(`${base}/recommendation`))) await render(); } }, "Get a suggestion") : null;
    return section(3, "What we suggest", el("p", { class: "muted" }, j.diagnosis ? "No suggestion has been made yet." : "A suggestion comes after we have looked into why."), ask);
  }
  if (!s.title) return section(3, "What we suggest", el("p", {}, s.headline), el("p", { class: "muted" }, s.why_this_one));
  return section(
    3,
    "What we suggest",
    el("h3", {}, s.title),
    el("p", {}, s.why_this_one),
    s.steps.length ? el("div", {}, el("strong", {}, "How to do it"), el("ol", {}, ...s.steps.map((t) => el("li", {}, t)))) : null,
    s.other_options ? el("p", { class: "muted" }, `${s.other_options} other ${s.other_options === 1 ? "way was" : "ways were"} considered. See them on the `, el("a", { href: `changes.html?org=${orgId}` }, "What changed page"), ".") : null,
  );
}

function decide(j) {
  const note = j.steps.find((s) => s.key === "decide").note;
  const waiting = ["open", "proposed"].includes(j.suggestion?.status);
  const holder = el("div", { id: "decision-card" });
  return section(
    4,
    "Your decision",
    note ? el("p", { class: waiting ? "" : "muted" }, note) : el("p", { class: "muted" }, "There is nothing to decide yet."),
    waiting ? holder : null,
    waiting && !j.can_decide ? el("p", { class: "muted" }, "Only an owner or manager can decide.") : null,
  );
}

function doing(j) {
  const a = j.action;
  if (!a) return section(5, "Getting it done", el("p", { class: "muted" }, "Once you accept the suggestion it becomes an action, with a person and a date."));
  return section(
    5,
    "Getting it done",
    el("h3", {}, a.title),
    el("p", {}, `${a.status_label}${a.owner ? `, with ${a.owner}` : ", with nobody yet"}${a.target_date ? `, to finish by ${ukDate(a.target_date)}` : ""}.`),
    el("p", {}, el("a", { class: "button secondary", href: `actions.html?org=${orgId}#${a.id}` }, "Open the action")),
  );
}

function result(j) {
  const o = j.outcome;
  if (o) {
    const bars =
      o.baseline_value !== null && o.measured_value !== null
        ? barChart(
            [
              { period_start: o.baseline_period, value: o.baseline_value, is_complete: true, status: "ok" },
              { period_start: o.measured_period, value: o.measured_value, is_complete: true, status: "ok" },
            ],
            o.unit,
            `${o.kpi_name} before and after`,
            { marked: o.measured_period },
          )
        : null;
    return section(
      6,
      "How it went",
      el("p", {}, el("span", { class: `badge ${OUTCOME_CLASS[o.outcome]}` }, o.label), " ", o.reason),
      bars,
      bars ? el("p", { class: "muted" }, `The left bar is ${o.kpi_name} when you accepted it (${formatValue(o.baseline_value, o.unit)}), the orange bar is afterwards (${formatValue(o.measured_value, o.unit)}).`) : null,
    );
  }
  const f = j.follow_up;
  return section(
    6,
    "How it went",
    el("p", { class: "muted" }, f ? `We will check how it went from ${ukDate(f.due_date)}, using the figures for that month.` : "We check how it went after the work is finished, and tell you in plain words, including when it cannot be measured yet."),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
