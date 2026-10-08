// Notifications: what you have been told, newest first, with what you have not read. You choose in
// the app and by email, for each kind of message, how you want to be told (security messages are
// always sent). Quiet hours, set for the whole business, hold non-urgent emails until morning.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put } from "../data.js";
import { el } from "../dom.js";
import { checkbox } from "../forms.js";
import { ukDateTime } from "../format.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const CATEGORY_TEXT = {
  sales: "Sales",
  financial: "Money",
  customer: "Customers",
  inventory: "Stock",
  marketing: "Marketing",
  forecast: "Forecasts",
  action: "Actions",
  data: "Your data",
  security: "Security",
};
const SEVERITY_TEXT = { info: "For information", low: "Low", medium: "Medium", high: "High", critical: "Critical" };
const SEVERITY_CLASS = { info: "health-not_enough_data", low: "health-not_enough_data", medium: "health-fair", high: "health-at_risk", critical: "health-at_risk" };

let base = "";
let orgId = "";
const list = el("div", { class: "stack" });
const prefs = el("div", { class: "stack" });

async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${org.id}`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${org.id}`;
  const readAll = el("button", { type: "button", class: "secondary", onclick: markAll }, "Mark everything as read");
  content.replaceChildren(
    el("div", { class: "actions" }, readAll, el("a", { class: "button secondary", href: `alerts.html?org=${org.id}` }, "Alert history and settings")),
    list,
    el("h2", {}, "How you want to be told"),
    prefs,
  );
  await Promise.all([load(), loadPrefs()]);
}

function page(link) {
  if (!link) return null;
  const [file, anchor] = link.split("#");
  return `${file}?org=${orgId}${anchor ? `#${anchor}` : ""}`;
}

async function load() {
  const box = await guard(message, () => api.get(`${base}/notifications`));
  if (!box) return;
  list.replaceChildren(el("p", { class: "muted" }, box.unread ? `${box.unread} you have not read.` : "You have read everything."));
  if (!box.items.length) return put(list, el("p", { class: "muted" }, "Nothing yet. When something needs your attention you will be told here, and by email if you want."));
  put(
    list,
    ...box.items.map((n) =>
      el(
        "div",
        { class: n.read ? "kpi" : "kpi unread" },
        el("div", {}, n.read ? null : el("strong", {}, "New · "), el("span", { class: `badge ${SEVERITY_CLASS[n.severity]}` }, SEVERITY_TEXT[n.severity]), " ", el("span", { class: "muted" }, `${CATEGORY_TEXT[n.category] ?? n.category} · ${ukDateTime(n.created_at)}`)),
        el("p", {}, el("strong", {}, n.title)),
        el("p", {}, n.body),
        el(
          "div",
          { class: "actions" },
          n.link ? el("a", { href: page(n.link), onclick: () => !n.read && api.post(`${base}/notifications/${n.id}/read`) }, "Take a look") : null,
          n.read ? null : el("button", { type: "button", class: "secondary", onclick: () => markOne(n.id) }, "Mark as read"),
        ),
      ),
    ),
  );
}

async function markOne(id) {
  await guard(message, () => api.post(`${base}/notifications/${id}/read`));
  load();
}

async function markAll() {
  await guard(message, () => api.post(`${base}/notifications/read-all`));
  load();
}

async function loadPrefs() {
  const rows = await guard(message, () => api.get(`${base}/notification-preferences`));
  if (!rows) return;
  const boxes = rows.map((p) => ({
    category: p.category,
    locked: p.locked,
    email: checkbox(`${p.category}-email`, "Email", { checked: p.email, disabled: p.locked }),
    inApp: checkbox(`${p.category}-app`, "In the app", { checked: p.in_app, disabled: p.locked }),
  }));
  const save = el("button", { type: "button" }, "Save");
  save.addEventListener("click", async () => {
    const preferences = {};
    for (const b of boxes) if (!b.locked) preferences[b.category] = { email: b.email.querySelector("input").checked, in_app: b.inApp.querySelector("input").checked };
    const saved = await guard(message, () => api.patch(`${base}/notification-preferences`, { preferences }));
    if (saved) {
      message.hidden = true;
      save.textContent = "Saved";
      setTimeout(() => (save.textContent = "Save"), 1500);
    }
  });
  prefs.replaceChildren(
    el("p", { class: "muted" }, "Only messages about something serious are emailed. Security messages are always sent, by email and in the app."),
    ...boxes.map((b) => el("div", { class: "actions" }, el("strong", {}, CATEGORY_TEXT[b.category] ?? b.category), b.email, b.inApp, b.locked ? el("span", { class: "muted" }, "always on") : null)),
    el("div", { class: "actions" }, save),
  );
}

const opened = await openBusiness(message);
if (opened) await start(opened);
