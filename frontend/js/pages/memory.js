// What we know: everything Vyterlix has learned about the business, in plain English: what is
// normal for each figure, patterns in customers, goals and seasons, what has been tried and how it
// went, and what was remembered when recent suggestions were made. The owner can also set limits
// on what may be suggested (cost, effort, speed, and actions never to suggest).

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put, table } from "../data.js";
import { el } from "../dom.js";
import { checkbox, field, select } from "../forms.js";
import { ukDateTime } from "../format.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const COST = [["", "No limit"], ["none", "Nothing"], ["low", "A little"], ["medium", "A moderate amount"]];
const EFFORT = [["", "No limit"], ["low", "A little effort"], ["medium", "A moderate amount of effort"]];
const OUTCOME_CLASS = {
  successful: "health-healthy",
  partially_successful: "health-fair",
  unsuccessful: "health-at_risk",
  inconclusive: "health-not_enough_data",
};
const OUTCOME_TEXT = { successful: "It worked", partially_successful: "It partly worked", unsuccessful: "It did not work", inconclusive: "We cannot tell" };

let base = "";
let canRefresh = false;
let isOwner = false;
let library = [];
const body = el("div", { class: "stack" });

async function start({ org }) {
  base = `/organizations/${org.id}/memory`;
  isOwner = org.role === "owner";
  canRefresh = isOwner || org.role === "manager";
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${org.id}`;
  library = (await guard(message, () => api.get(`/organizations/${org.id}/interventions`))) ?? [];
  const refresh = canRefresh ? el("div", { class: "actions" }, el("button", { type: "button", class: "secondary", onclick: rebuild }, "Refresh what we know")) : null;
  content.replaceChildren(
    el("p", { class: "muted" }, "This is what Vyterlix has learned about your business from your own records and from what you have tried. It is used to rank the next suggestion, and nothing here is shared with anyone else."),
  );
  put(content, refresh, body);
  await load();
}

async function rebuild() {
  const r = await guard(message, () => api.post(`${base}/rebuild`));
  if (r) {
    message.hidden = true;
    await load();
  }
}

async function load() {
  const m = await guard(message, () => api.get(base));
  if (!m) return;
  body.replaceChildren();
  put(
    body,
    section("What is normal for you", m.normal_ranges, "Not enough months of figures yet. Half a year is needed before we can say what is normal.", true),
    section("Patterns in your customers", m.customer_patterns, "Nothing to say yet. Bring in your sales and customers first."),
    section("Your goals", m.goals, "You have not set any goals."),
    section("Your busy and quiet times", m.seasons, "No seasons set."),
    limitsSection(m.constraints),
    learnedSection(m),
    recentSection(m.recent_use),
    m.last_rebuilt ? el("p", { class: "muted" }, `What we know about your figures was last worked out on ${ukDateTime(m.last_rebuilt)}.`) : null,
  );
}

function section(title, items, empty, collapsed = false) {
  const list = el("ul", {}, ...items.map((i) => el("li", {}, i.statement)));
  const shown = !items.length ? el("p", { class: "muted" }, empty) : collapsed ? el("details", {}, el("summary", {}, `Show the ${items.length} figures`), list) : list;
  return el("div", { class: "stack" }, el("h2", {}, title), shown);
}

function limitsSection(c) {
  const cost = select("max_cost_level", COST, { selected: c.max_cost_level ?? "", disabled: !isOwner });
  const effort = select("max_effort", EFFORT, { selected: c.max_effort ?? "", disabled: !isOwner });
  const quick = checkbox("quick_results_only", "Only suggest actions that show within a month", { checked: c.quick_results_only, disabled: !isOwner });
  const never = library.map((a) => checkbox(`never-${a.code}`, a.name, { checked: c.excluded_actions.includes(a.code), disabled: !isOwner, "data-code": a.code }));
  const save = el("button", { type: "submit" }, "Save my limits");
  const form = el(
    "form",
    { class: "stack" },
    field("The most an action may cost", cost),
    field("The most effort you can give", effort),
    quick,
    el("div", { class: "stack" }, el("strong", {}, "Never suggest these"), ...never),
    isOwner ? el("div", { class: "actions" }, save) : el("p", { class: "muted" }, "Only the owner can change these limits."),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const excluded = [...form.querySelectorAll("input[data-code]")].filter((b) => b.checked).map((b) => b.dataset.code);
    const saved = await guard(message, () =>
      api.put(`${base}/constraints`, {
        max_cost_level: cost.value || null,
        max_effort: effort.value || null,
        excluded_actions: excluded,
        quick_results_only: quick.querySelector("input").checked,
      }),
    );
    if (saved) {
      message.hidden = true;
      save.textContent = "Saved";
      setTimeout(() => (save.textContent = "Save my limits"), 1500);
    }
  });
  return el(
    "div",
    { class: "stack" },
    el("h2", {}, "Your limits"),
    el("p", { class: "muted" }, "Suggestions that break these are left out, and the suggestion says what was left out and why."),
    form,
  );
}

function learnedSection(m) {
  return el(
    "div",
    { class: "stack" },
    el("h2", {}, "What we have learned from what you tried"),
    m.lessons.length
      ? el("ul", {}, ...m.lessons.map((l) => el("li", {}, el("span", { class: `badge ${OUTCOME_CLASS[l.outcome]}` }, OUTCOME_TEXT[l.outcome]), " ", l.lesson)))
      : el("p", { class: "muted" }, "Nothing yet. When an action you took has been checked, what happened is kept here."),
    m.patterns.length
      ? table(
          ["Kind of action", "Figure", "Worked", "Partly", "Did not", "Could not tell", "Usually achieved"],
          m.patterns.map((p) => [p.action, p.kpi_name, p.successful, p.partially_successful, p.unsuccessful, p.inconclusive, p.average_achieved_pct === null ? "–" : `${p.average_achieved_pct}% of what was expected`]),
        )
      : null,
  );
}

function recentSection(uses) {
  return el(
    "div",
    { class: "stack" },
    el("h2", {}, "What we remembered when we last made a suggestion"),
    uses.length
      ? el("ul", {}, ...uses.map((u) => el("li", {}, el("span", { class: "muted" }, ukDateTime(u.created_at)), el("ul", {}, ...u.used.map((x) => el("li", {}, x.statement))))))
      : el("p", { class: "muted" }, "Nothing yet. Your limits and what you have learned are used, and listed here, whenever a suggestion is made."),
  );
}

const opened = await openBusiness(message);
if (opened) await start(opened);
