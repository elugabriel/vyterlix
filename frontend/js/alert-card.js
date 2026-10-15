// An alert and what to do about it, in one place (client feedback, section 5): what happened in
// everyday words, why it matters, how urgent, a small chart, what we suggest, and the buttons to
// decide (Accept, Edit, Assign, Dismiss). Accepting makes a real action, linked to the change.

import { api } from "./auth.js";
import { guard, put } from "./data.js";
import { el } from "./dom.js";
import { field, input, select } from "./forms.js";
import { ukDate } from "./format.js";
import { barChart } from "./kpi.js";

const message = document.getElementById("message");
const PRIORITY_CLASS = { high: "health-at_risk", medium: "health-fair", low: "health-not_enough_data" };
const CATEGORY_TEXT = { sales: "Sales", financial: "Financial Health", customer: "Customers", inventory: "Stock", marketing: "Marketing", forecast: "Forecasts", action: "Actions", data: "Your data", security: "Security" };
const EFFORT = { low: "A little effort", medium: "A moderate amount of effort", high: "A lot of effort" };
const COST = { none: "Costs nothing", low: "Costs a little", medium: "Costs a moderate amount", high: "Costs a lot" };

let members = null;

async function people(orgId) {
  if (members === null) {
    const rows = (await api.get(`/organizations/${orgId}/members`).catch(() => [])) ?? [];
    members = rows.filter((m) => m.status === "active").map((m) => [m.user_id, m.full_name]);
  }
  return members;
}

/** Show the card for one alert inside `holder`. `onChange` is called after a decision, so the
 * page around it can refresh its counts and list. */
export async function mountCard(holder, { orgId, alertId, pageLink, onChange = () => {} }) {
  const base = `/organizations/${orgId}/alerts`;
  const card = await guard(message, () => api.get(`${base}/${alertId}/card`));
  if (!card) return null;
  draw(holder, card, { orgId, base, alertId, pageLink, onChange });
  return card;
}

function draw(holder, card, ctx) {
  const a = card.alert;
  const body = el("div", { class: "stack alert-card" });
  const decide = async (verb, payload) => {
    const done = await guard(message, () => api.post(`${ctx.base}/${ctx.alertId}/${verb}`, payload));
    if (done) {
      message.hidden = true;
      draw(holder, done, ctx);
      ctx.onChange(done);
    }
  };

  put(
    body,
    el(
      "p",
      { class: "alert-card-meta" },
      el("span", { class: `badge ${PRIORITY_CLASS[card.priority.level]}` }, `${card.priority.label} priority`),
      " ",
      el("span", { class: "muted" }, CATEGORY_TEXT[a.category] ?? a.category),
    ),
    el("h2", {}, card.headline),
    card.state === "other" ? el("p", {}, card.happened) : el("p", { class: `plain-says plain-${card.tone}` }, card.happened),
    card.why_it_matters ? el("p", { class: "muted" }, card.why_it_matters) : null,
    card.numbers ? el("p", { class: "muted" }, card.numbers) : null,
    card.season_note ? el("p", { class: "muted" }, card.season_note) : null,
    el("p", { class: "muted", title: "How urgent this is, from its likely effect, how soon it matters and how strong the evidence is" }, card.priority.reason),
    chartFor(card),
    card.evidence.length ? el("div", {}, el("strong", {}, "What we found"), el("ul", {}, ...card.evidence.map((e) => el("li", {}, e)))) : null,
    ...stateBlocks(card, ctx, decide),
    card.event_id ? el("p", {}, el("a", { href: `changes.html?org=${ctx.orgId}#${card.event_id}` }, "See why it happened and the full explanation")) : null,
  );
  holder.replaceChildren(body);
}

function chartFor(card) {
  if (!card.chart || !card.chart.points.length) return null;
  const values = card.chart.points.map((p) => ({ ...p, status: "ok" }));
  return el("div", {}, barChart(values, card.chart.unit, `${card.chart.name} over the last months`, { marked: card.chart.marked }), el("p", { class: "muted" }, `${card.chart.name} by month. The darker bar is the month this is about.`));
}

function stateBlocks(card, ctx, decide) {
  switch (card.state) {
    case "open":
      return [suggestion(card.suggestion), card.can_decide ? decisions(card, ctx, decide) : el("p", { class: "muted" }, "Only an owner or manager can accept or dismiss this.")];
    case "proposed":
      return [suggestion(card.suggestion), actionBlock(card.action, "Put forward for approval. It waits for the owner to approve it.", ctx)];
    case "accepted":
      return [actionBlock(card.action, "You accepted this, so it is now an action.", ctx)];
    case "dismissed":
      return [el("p", { class: "muted" }, `You decided not to act on this. ${card.dismissed_reason ?? ""}`.trim())];
    case "good_news":
      return [el("p", {}, el("strong", {}, "Good news. "), "This change is in your favour, so there is nothing to put right.")];
    case "no_suggestion":
      return [el("p", { class: "muted" }, "We cannot say what to do about this yet. Open the explanation to see what we found.")];
    case "not_yet":
      return [el("p", { class: "muted" }, "A suggestion has not been made yet. An owner or manager can open this alert to get one.")];
    default:
      return [
        card.next_step ? el("p", {}, el("strong", {}, "Next step: "), card.next_step.text) : null,
        card.next_step && card.next_step.link ? el("p", {}, el("a", { class: "button secondary", href: ctx.pageLink(card.next_step.link) }, "Open it")) : null,
      ];
  }
}

function suggestion(s) {
  if (!s) return null;
  return el(
    "div",
    { class: "suggestion" },
    el("strong", {}, "What we suggest"),
    el("h3", {}, s.title),
    el("p", {}, s.description),
    el("p", { class: "muted" }, `${EFFORT[s.effort]} · ${COST[s.cost_level]} · usually starts to show in about ${s.days_to_effect} days`),
    s.why_this_one ? el("p", { class: "muted" }, s.why_this_one) : null,
    s.steps.length ? el("details", {}, el("summary", {}, "See the steps"), el("ol", {}, ...s.steps.map((t) => el("li", {}, t)))) : null,
    s.other_options ? el("p", { class: "muted" }, `We also looked at ${s.other_options} other ${s.other_options === 1 ? "way" : "ways"}; they are on the explanation page.`) : null,
  );
}

function actionBlock(action, intro, ctx) {
  if (!action) return el("p", {}, intro);
  return el(
    "div",
    { class: "suggestion" },
    el("p", {}, intro),
    el("h3", {}, action.title),
    el("p", {}, `Assigned to ${action.owner ?? "nobody yet"}${action.target_date ? `, due by ${ukDate(action.target_date)}` : ""}.`),
    action.check_from ? el("p", { class: "muted" }, `We will check how it went from ${ukDate(action.check_from)}.`) : null,
    el("p", {}, el("a", { href: `actions.html?org=${ctx.orgId}#${action.id}` }, "Open the action")),
  );
}

/** Accept, Edit, Assign and Dismiss. Accept takes it as suggested, for you; the others open a small form first. */
function decisions(card, ctx, decide) {
  const s = card.suggestion;
  const panel = el("div", { class: "stack" });
  const note = el("p", { class: "muted" }, `If you accept it as it is: it is assigned to you, due by ${ukDate(s.due_by)}, and we check the result after that.`);
  const open = (which) => panel.replaceChildren(...which());

  const accept = el("button", { type: "button", onclick: () => decide("accept", {}) }, "Accept");

  const edit = () => {
    const title = input("title", { value: s.title, maxlength: 200 });
    const description = el("textarea", { name: "description", rows: 3, maxlength: 2000 }, s.description);
    const steps = el("textarea", { name: "steps", rows: 5 }, s.steps.join("\n"));
    const go = el("button", { type: "button" }, "Accept with my changes");
    go.addEventListener("click", () => {
      const lines = steps.value.split("\n").map((t) => t.trim()).filter(Boolean);
      decide("accept", { title: title.value.trim() || undefined, description: description.value.trim() || undefined, steps: lines.map((text) => ({ text })) });
    });
    return [field("What to do", title), field("More detail", description), field("Steps (one on each line)", steps), el("div", { class: "actions" }, go)];
  };

  const assign = () => {
    const who = select("owner", [], { placeholder: "Me" });
    const due = input("due", { type: "date", value: s.due_by });
    const go = el("button", { type: "button" }, "Accept and assign");
    people(ctx.orgId).then((list) => {
      who.append(...list.map(([id, name]) => el("option", { value: id }, name)));
    });
    go.addEventListener("click", () => decide("accept", { owner_user_id: who.value || undefined, target_date: due.value || undefined }));
    return [field("Who should do it?", who), field("Due by", due), el("div", { class: "actions" }, go)];
  };

  const dismiss = () => {
    const reason = input("reason", { maxlength: 500, placeholder: "Why not? (optional, kept for next time)" });
    const go = el("button", { type: "button", class: "secondary" }, "Dismiss it");
    go.addEventListener("click", () => decide("dismiss", { reason: reason.value.trim() || null }));
    return [field("Your reason", reason), el("div", { class: "actions" }, go)];
  };

  return el(
    "div",
    { class: "stack" },
    note,
    el(
      "div",
      { class: "actions" },
      accept,
      el("button", { type: "button", class: "secondary", onclick: () => open(edit) }, "Edit"),
      el("button", { type: "button", class: "secondary", onclick: () => open(assign) }, "Assign"),
      el("button", { type: "button", class: "secondary", onclick: () => open(dismiss) }, "Dismiss"),
    ),
    panel,
  );
}
