// Alerts: everything that has needed attention, with how serious it was, what happened to it, and
// the settings for each kind (the owner can switch kinds on and off, change how serious they are and
// their thresholds; security alerts cannot be switched off). Owners and managers can say they have
// seen an alert or close it.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put, table } from "../data.js";
import { el } from "../dom.js";
import { checkbox, field, input, select } from "../forms.js";
import { ukDateTime } from "../format.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const CATEGORY_TEXT = { sales: "Sales", financial: "Money", customer: "Customers", inventory: "Stock", marketing: "Marketing", forecast: "Forecasts", action: "Actions", data: "Your data", security: "Security" };
const SEVERITY = [["info", "For information"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["critical", "Critical"]];
const SEVERITY_TEXT = Object.fromEntries(SEVERITY);
const SEVERITY_CLASS = { info: "health-not_enough_data", low: "health-not_enough_data", medium: "health-fair", high: "health-at_risk", critical: "health-at_risk" };
const STATUS_TEXT = { open: "Open", acknowledged: "Seen", resolved: "Closed" };
const MIN_SIZE = [["notable", "Noticeable or bigger"], ["major", "Big changes only"]];

let base = "";
let orgId = "";
let canAct = false;
let isOwner = false;
let filters = { status: "", severity: "", category: "" };
const counts = el("p", { class: "muted" });
const list = el("div", { class: "stack" });
const detail = el("div", { class: "stack" });
const settings = el("div", { class: "stack" });

async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${org.id}/alerts`;
  isOwner = org.role === "owner";
  canAct = isOwner || org.role === "manager";
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${org.id}`;
  const pick = (name, options, label) => {
    const control = select(name, options, { placeholder: label });
    control.addEventListener("change", () => {
      filters[name] = control.value;
      load();
    });
    return control;
  };
  const lookNow = canAct ? el("button", { type: "button", class: "secondary", onclick: evaluate }, "Look for things that need attention now") : null;
  content.replaceChildren(
    counts,
    el("div", { class: "actions" }, pick("status", Object.entries(STATUS_TEXT), "Any state"), pick("severity", SEVERITY, "Any seriousness"), pick("category", Object.entries(CATEGORY_TEXT), "Any kind")),
    lookNow ? el("div", { class: "actions" }, lookNow, el("a", { class: "button secondary", href: `notifications.html?org=${org.id}` }, "My notifications")) : null,
    list,
    detail,
    el("h2", {}, "Settings for each kind of alert"),
    settings,
  );
  await Promise.all([load(), loadCounts(), loadRules()]);
  openFromHash();
  window.addEventListener("hashchange", openFromHash);
}

async function loadCounts() {
  const c = await guard(message, () => api.get(`${base}/summary`));
  if (c) counts.textContent = `${c.open} open · ${c.acknowledged} seen · ${c.resolved} closed · ${c.high_or_critical_open} serious ones not yet closed`;
}

async function load() {
  const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
  const rows = await guard(message, () => api.get(`${base}?${params}`));
  if (!rows) return;
  list.replaceChildren(
    table(
      ["How serious", "What", "Kind", "State", "Last seen"],
      rows.map((a) => [
        el("span", { class: `badge ${SEVERITY_CLASS[a.severity]}` }, SEVERITY_TEXT[a.severity]),
        el("a", { href: `#${a.id}` }, a.title),
        CATEGORY_TEXT[a.category] ?? a.category,
        a.occurrences > 1 && a.status !== "resolved" ? `${STATUS_TEXT[a.status]} (seen on ${a.occurrences} days)` : STATUS_TEXT[a.status],
        ukDateTime(a.last_seen_at),
      ]),
      { empty: "No alerts. When something needs your attention it will appear here." },
    ),
  );
}

async function evaluate() {
  const r = await guard(message, () => api.post(`${base}/evaluate`));
  if (r) {
    message.hidden = true;
    await Promise.all([load(), loadCounts()]);
  }
}

async function openFromHash() {
  const id = location.hash.slice(1);
  if (!/^[0-9a-f-]{36}$/i.test(id)) return detail.replaceChildren();
  const a = await guard(message, () => api.get(`${base}/${id}`));
  if (a) show(a);
}

function pageLink(link) {
  if (!link) return null;
  const [file, anchor] = link.split("#");
  return `${file}?org=${orgId}${anchor ? `#${anchor}` : ""}`;
}

async function act(id, verb, note) {
  const a = await guard(message, () => api.post(`${base}/${id}/${verb}`, { note: note || null }));
  if (a) {
    message.hidden = true;
    show(a);
    load();
    loadCounts();
  }
}

function show(a) {
  const note = input("note", { maxlength: 500, placeholder: "A note (optional)" });
  detail.replaceChildren();
  put(
    detail,
    el("h2", {}, a.title),
    el("p", {}, el("span", { class: `badge ${SEVERITY_CLASS[a.severity]}` }, SEVERITY_TEXT[a.severity]), " ", el("span", { class: "muted" }, `${CATEGORY_TEXT[a.category] ?? a.category} · ${STATUS_TEXT[a.status]}`)),
    el("p", {}, a.body),
    a.link ? el("p", {}, el("a", { href: pageLink(a.link) }, "See the figures behind it")) : null,
    canAct && a.status !== "resolved" ? note : null,
    canAct && a.status !== "resolved"
      ? el("div", { class: "actions" }, a.status === "open" ? el("button", { type: "button", onclick: () => act(a.id, "acknowledge", note.value) }, "I have seen this") : null, el("button", { type: "button", class: "secondary", onclick: () => act(a.id, "resolve", note.value) }, "Close it"))
      : null,
    el("strong", {}, "What happened to it"),
    el("ul", {}, ...a.events.map((e) => el("li", {}, el("span", { class: "muted" }, `${ukDateTime(e.created_at)} · ${e.user ?? "Vyterlix"} · `), { raised: "Raised", repeated: "Seen again", acknowledged: "Seen", resolved: "Closed", reopened: "Reopened" }[e.kind], e.note ? `: ${e.note}` : ""))),
  );
}

async function loadRules() {
  const rows = await guard(message, () => api.get(`${base}/rules`));
  if (!rows) return;
  settings.replaceChildren(
    el("p", { class: "muted" }, isOwner ? "Choose what you want to be told about. A change applies the next time we look." : "Only the owner can change these."),
    ...rows.map(rule),
  );
}

function rule(r) {
  const enabled = checkbox("enabled", r.name, { checked: r.enabled, disabled: !isOwner || r.always_on });
  const severity = select("severity", SEVERITY, { selected: r.severity, disabled: !isOwner });
  const controls = Object.entries(r.params).map(([key, value]) => {
    const isSize = key === "min_size";
    const control = isSize ? select(key, MIN_SIZE, { selected: value, disabled: !isOwner }) : input(key, { type: "number", value, min: 1, max: 1000, disabled: !isOwner });
    return [key, field(r.params_help[key] ?? key, control), control, isSize];
  });
  const save = el("button", { type: "button", class: "secondary" }, "Save");
  save.addEventListener("click", async () => {
    const params = {};
    for (const [key, , control, isSize] of controls) params[key] = isSize ? control.value : Number(control.value);
    const saved = await guard(message, () => api.put(`${base}/rules/${r.code}`, { enabled: enabled.querySelector("input").checked, severity: severity.value, params }));
    if (saved) {
      message.hidden = true;
      save.textContent = "Saved";
      setTimeout(() => (save.textContent = "Save"), 1500);
    }
  });
  return el(
    "details",
    {},
    el("summary", {}, `${r.name} (${CATEGORY_TEXT[r.category] ?? r.category}${r.always_on ? ", always on" : r.enabled ? "" : ", off"})`),
    el("div", { class: "stack" }, el("p", { class: "muted" }, r.description), enabled, field("How serious", severity), ...controls.map(([, f]) => f), isOwner ? el("div", { class: "actions" }, save) : null),
  );
}

const opened = await openBusiness(message);
if (opened) await start(opened);
