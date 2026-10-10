// Today: the front screen. It opens with what needs attention, most serious first, each with a
// button that takes you to where it is dealt with; only after that does it show how the business
// is doing. Owners are shown everything, a Manager what is in their own area, and a Viewer is told
// what is going on but is given nothing to do. Everything here was worked out elsewhere; this page
// only gathers it.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put } from "../data.js";
import { el } from "../dom.js";
import { formatValue, periodLabel } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const SEVERITY_TEXT = { info: "For information", low: "Low", medium: "Medium", high: "High", critical: "Critical" };
const SEVERITY_CLASS = { info: "health-not_enough_data", low: "health-not_enough_data", medium: "health-fair", high: "health-at_risk", critical: "health-at_risk" };
const KIND_TEXT = { approval: "Waiting for you", action_overdue: "Late", alert: "Alert", follow_up: "Check the result", action_due_soon: "Due soon", suggestion: "Suggestion" };
const BUTTON = { approval: "Review it", action_overdue: "Open it", alert: "See why", follow_up: "Open it", action_due_soon: "Open it", suggestion: "See the options" };
const HEALTH_TEXT = { healthy: "Healthy", fair: "Fair", needs_attention: "Needs attention", at_risk: "At risk", not_enough_data: "Not enough data" };

let orgId = "";

function page(link) {
  const [file, anchor] = link.split("#");
  return `${file}?org=${orgId}${anchor ? `#${anchor}` : ""}`;
}

function hero(d) {
  const h = d.health;
  const change = !h || h.previous_score === null ? "" : h.score === h.previous_score ? "No change on the month before" : `${h.score > h.previous_score ? "Up" : "Down"} from ${h.previous_score} the month before`;
  return el(
    "section",
    { class: "hero" },
    el("div", { class: "stack" },
      el("div", { class: "muted", style: "color:rgba(255,255,255,.7);text-transform:uppercase;letter-spacing:.08em;font-size:.75rem;font-weight:650" }, "Today"),
      el("h2", {}, d.headline),
      h && h.weakest ? el("p", {}, `The area pulling your health down most is ${h.weakest}.`) : null,
    ),
    h
      ? el("div", { class: "hero-score" },
          el("div", {}, el("div", { class: "number" }, String(h.score)), el("div", { class: "of" }, "out of 100")),
          el("div", { class: "stack" },
            el("span", { class: "badge" }, HEALTH_TEXT[h.status] ?? h.status),
            el("div", { class: "of" }, `${change ? `${change} · ` : ""}${periodLabel(h.period, "month")}`),
            el("a", { href: page("health.html") }, "See the whole picture"),
          ),
        )
      : null,
  );
}

function attention(d) {
  if (!d.attention.length) {
    return el("section", { class: "stack" }, el("div", { class: "section-title" }, el("h2", {}, "Needs your attention")), el("p", { class: "muted" }, "Nothing needs you right now."));
  }
  const items = d.attention.map((i) =>
    el(
      "div",
      { class: `attention-item sev-${i.severity}` },
      el("div", {}, el("span", { class: `badge ${SEVERITY_CLASS[i.severity]}` }, SEVERITY_TEXT[i.severity]), " ", el("span", { class: "muted" }, KIND_TEXT[i.kind])),
      el("p", {}, el("strong", {}, i.title)),
      el("p", { class: "muted" }, i.detail),
      el("a", { class: "button secondary", href: page(i.link) }, i.can_act ? BUTTON[i.kind] : "Take a look"),
    ),
  );
  return el(
    "section",
    {},
    el("div", { class: "section-title" }, el("h2", {}, "Needs your attention")),
    el("div", { class: "attention" }, ...items),
    d.more_attention ? el("p", { class: "muted", style: "margin-top:12px" }, `${d.more_attention} more. `, el("a", { href: page("alerts.html") }, "See them all on the Alerts page")) : null,
  );
}

function figures(list) {
  if (!list.length) return null;
  return el(
    "section",
    { class: "stack" },
    el("div", { class: "section-title" }, el("h2", {}, "Your key figures")),
    el(
      "div",
      { class: "figures" },
      ...list.map((f) => {
        const pct = f.change_pct === null ? null : Number(f.change_pct);
        const good = pct === null || f.direction === "neutral" ? null : (pct >= 0) === (f.direction === "up_good");
        return el(
          "a",
          { class: "figure", href: page(`kpis.html#${f.code}`) },
          el("div", { class: "muted" }, f.name),
          el("div", { class: "big" }, f.value === null ? "–" : formatValue(f.value, f.unit)),
          pct === null || f.unit === "percent" ? el("div", { class: "muted" }, periodLabel(f.period, "month")) : el("div", { class: good === null ? "muted" : good ? "status-ok" : "status-bad" }, `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(1)}% on the month before`),
        );
      }),
    ),
  );
}

function setup(s) {
  if (!s) return null;
  return el(
    "section",
    { class: "card stack" },
    el("h2", { style: "margin-top:0" }, "Finish setting up"),
    el("p", {}, `${s.done} of ${s.total} steps are done. The more you tell us, the better the figures and the suggestions.`),
    el("a", { class: "button", href: page("onboarding.html") }, s.ready ? "Finish setting up" : "Carry on setting up"),
  );
}

async function start({ org }) {
  orgId = org.id;
  document.getElementById("org-name").textContent = org.name;
  const d = await guard(message, () => api.get(`/organizations/${org.id}/dashboard`));
  if (!d) return;
  content.replaceChildren();
  put(content, hero(d), setup(d.setup), attention(d), figures(d.figures));
  if (!d.health && !d.figures.length) {
    put(content, el("p", { class: "muted" }, "There are no figures yet. Bring in your sales, costs and customers from "), el("a", { href: page("data.html") }, "Your data"), el("span", { class: "muted" }, " and they will appear here."));
  }
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
