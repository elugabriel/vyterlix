// Admin: for Vyterlix platform staff only. Look after businesses, people, plans and the audit trail.
// Staff see accounts and plans, never the figures inside a business. Every look at a business or a
// person, and every change, is written to the audit log; each change asks for a reason first.
// Support staff can look; only admins see the buttons that change things (the server decides anyway).

import { ApiError } from "../api.js";
import { api, logout, requireLogin } from "../auth.js";
import { guard, table } from "../data.js";
import { el } from "../dom.js";
import { field, input, select } from "../forms.js";
import { ukDate, ukDateTime } from "../format.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const TABS = [["organizations", "Businesses"], ["users", "People"], ["audit", "Audit log"], ["plans", "Plans"]];
const FEATURE_LABEL = { members: "Team members", integrations: "Connections", scheduled_reports: "Scheduled reports", ai_assistant: "AI assistant" };

let canChange = false;
let tab = "organizations";
const tabBar = el("div", { class: "tabs", role: "tablist" });
const panel = el("div", { class: "stack" });

const user = await requireLogin();
document.getElementById("user-name").textContent = user.full_name;
document.getElementById("logout").addEventListener("click", async () => {
  await logout();
  location.assign("login.html");
});

/** Ask why, in a small box, before anything is changed. Resolves to the reason, or null if backed out. */
function askReason(question) {
  return new Promise((resolve) => {
    const box = el("textarea", { name: "reason", rows: 3, maxlength: 500, "aria-label": "Reason" });
    const ok = el("button", { type: "submit" }, "Confirm");
    const cancel = el("button", { type: "button", class: "secondary" }, "Cancel");
    const error = el("p", { class: "field-error", hidden: true }, "Please give a reason of at least five characters.");
    const dialog = el("dialog", {}, el("form", { method: "dialog", class: "stack" }, el("p", {}, question), field("Reason (kept in the audit log)", box), error, el("div", { class: "actions" }, ok, cancel)));
    let answer = null;
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(answer);
    });
    cancel.addEventListener("click", () => dialog.close());
    dialog.querySelector("form").addEventListener("submit", (event) => {
      if (box.value.trim().length < 5) {
        event.preventDefault();
        error.hidden = false;
        return;
      }
      answer = box.value.trim();
    });
    document.body.append(dialog);
    dialog.showModal();
    box.focus();
  });
}

/** Run a change after asking for a reason; show the result and reload the panel. */
async function change(question, action, done, reload) {
  const reason = await askReason(question);
  if (!reason) return;
  const result = await guard(message, () => action(reason));
  if (result !== undefined) {
    showMessage(message, "success", done);
    await reload();
  }
}

function pager(page, limit, onPage) {
  const first = page.offset ?? 0;
  const back = el("button", { type: "button", class: "secondary" }, "Previous");
  const next = el("button", { type: "button", class: "secondary" }, "Next");
  back.disabled = first === 0;
  next.disabled = first + limit >= page.total;
  back.addEventListener("click", () => onPage(Math.max(0, first - limit)));
  next.addEventListener("click", () => onPage(first + limit));
  const from = page.total === 0 ? 0 : first + 1;
  return el("div", { class: "pager" }, back, el("span", { class: "muted" }, `${from} to ${Math.min(first + limit, page.total)} of ${page.total}`), next);
}

const badge = (text) => el("span", { class: "badge" }, text);

// --- businesses ---------------------------------------------------------------------------------------

async function showOrganizations() {
  const search = input("q", { type: "text", placeholder: "Search by name", "aria-label": "Search businesses" });
  const status = select("status", [["", "Any status"], ["active", "Active"], ["suspended", "Suspended"], ["closed", "Closed"]], { "aria-label": "Status" });
  const list = el("div", { class: "stack" });
  const detail = el("div", { class: "stack" });
  const limit = 25;
  async function load(offset = 0) {
    const params = new URLSearchParams({ limit, offset });
    if (search.value.trim()) params.set("q", search.value.trim());
    if (status.value) params.set("status", status.value);
    const page = await guard(message, () => api.get(`/admin/organizations?${params}`));
    if (!page) return;
    page.offset = offset;
    list.replaceChildren(
      table(
        ["Business", "Status", "People", "Plan", ""],
        page.items.map((o) => {
          const open = el("button", { type: "button", class: "link" }, "Open");
          open.addEventListener("click", () => openOrganization(o.id));
          return [o.name, badge(o.status), String(o.member_count), o.plan_name ? `${o.plan_name} (${o.plan_status})` : "Not looked at yet", open];
        }),
        { empty: "No businesses match." },
      ),
      pager(page, limit, load),
    );
  }
  async function openOrganization(id) {
    const o = await guard(message, () => api.get(`/admin/organizations/${id}`));
    if (!o) return;
    const reload = async () => {
      await openOrganization(id);
      await load();
    };
    const buttons = [];
    if (canChange) {
      if (o.status === "active") buttons.push(actionButton("Suspend this business", () => change("Suspend this business? Its people will be locked out until you reactivate it.", (reason) => api.post(`/admin/organizations/${id}/suspend`, { reason }), "The business is suspended.", reload), true));
      if (o.status === "suspended") buttons.push(actionButton("Reactivate this business", () => change("Let this business's people back in?", (reason) => api.post(`/admin/organizations/${id}/reactivate`, { reason }), "The business is active again.", reload)));
      if (o.subscription && o.subscription.status !== "active" && o.subscription.status !== "past_due" && o.subscription.status !== "canceled") buttons.push(extendTrialButton(id, reload));
    }
    detail.replaceChildren(
      el("div", { class: "kpi stack" },
        el("div", { class: "kpi-name" }, o.name, " ", badge(o.status)),
        el("p", { class: "muted" }, `Started ${ukDate(o.created_at)}${o.created_by_email ? ` by ${o.created_by_email}` : ""}. Last activity ${o.last_activity_at ? ukDateTime(o.last_activity_at) : "none"}.`),
        o.subscription
          ? el("p", {}, `Plan: ${o.subscription.plan_name} (${o.subscription.status}, paid ${o.subscription.interval === "year" ? "yearly" : "monthly"}${o.subscription.provider !== "none" ? ` through ${o.subscription.provider}` : ""})${o.subscription.trial_ends_at ? `. Trial ends ${ukDate(o.subscription.trial_ends_at)}` : ""}${o.subscription.current_period_end ? `. Period ends ${ukDate(o.subscription.current_period_end)}` : ""}.`)
          : el("p", { class: "muted" }, "Nobody has looked at the plan yet, so no trial has started."),
        el("p", {}, `Using ${o.usage.members} of the places for people, ${o.usage.integrations} connections and ${o.usage.scheduled_reports} scheduled reports.`),
        buttons.length ? el("div", { class: "actions" }, ...buttons) : null,
      ),
      el("h3", {}, "People"),
      table(
        ["Name", "Email", "Role", "Status", "Last login", ""],
        o.members.map((m) => [m.full_name, m.email, m.role, m.user_active ? m.status : "account locked", m.last_login_at ? ukDateTime(m.last_login_at) : "never", canChange ? roleControl(id, m, reload) : ""]),
        { empty: "No members." },
      ),
    );
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const go = el("button", { type: "button" }, "Search");
  go.addEventListener("click", () => load());
  search.addEventListener("keydown", (event) => event.key === "Enter" && load());
  status.addEventListener("change", () => load());
  panel.replaceChildren(el("div", { class: "actions" }, search, status, go), list, detail);
  await load();
}

function actionButton(label, onClick, danger = false) {
  const button = el("button", { type: "button", class: danger ? "danger" : "secondary" }, label);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await onClick();
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function extendTrialButton(id, reload) {
  return actionButton("Give more trial time", async () => {
    const days = Number(prompt("How many more days of free trial (1 to 90)?", "7"));
    if (!Number.isInteger(days) || days < 1 || days > 90) return;
    await change(`Give this business ${days} more days of free trial?`, (reason) => api.post(`/admin/organizations/${id}/extend-trial`, { days, reason }), `The trial now runs ${days} days longer.`, reload);
  });
}

function roleControl(orgId, member, reload) {
  const roles = select("role", [["owner", "Owner"], ["manager", "Manager"], ["viewer", "Viewer"]], { selected: member.role, "aria-label": `Role of ${member.email}` });
  const apply = el("button", { type: "button", class: "secondary" }, "Change role");
  apply.addEventListener("click", async () => {
    if (roles.value === member.role) return;
    await change(`Make ${member.email} ${roles.value === "owner" ? "an owner" : `a ${roles.value}`}?`, (reason) => api.put(`/admin/organizations/${orgId}/members/${member.user_id}/role`, { role: roles.value, reason }), "The role is changed.", reload);
  });
  return el("div", { class: "actions" }, roles, apply);
}

// --- people ---------------------------------------------------------------------------------------------------

async function showUsers() {
  const search = input("q", { type: "text", placeholder: "Search by name or email", "aria-label": "Search people" });
  const list = el("div", { class: "stack" });
  const detail = el("div", { class: "stack" });
  const limit = 25;
  async function load(offset = 0) {
    const params = new URLSearchParams({ limit, offset });
    if (search.value.trim()) params.set("q", search.value.trim());
    const page = await guard(message, () => api.get(`/admin/users?${params}`));
    if (!page) return;
    page.offset = offset;
    list.replaceChildren(
      table(
        ["Name", "Email", "Businesses", "Verified", "Last login", "Account", ""],
        page.items.map((u) => {
          const open = el("button", { type: "button", class: "link" }, "Open");
          open.addEventListener("click", () => openUser(u.id));
          return [u.full_name, u.email, String(u.organization_count), u.email_verified ? "Yes" : "No", u.last_login_at ? ukDateTime(u.last_login_at) : "never", u.is_active ? (u.staff_role ? `Active, ${u.staff_role} staff` : "Active") : "Locked", open];
        }),
        { empty: "Nobody matches." },
      ),
      pager(page, limit, load),
    );
  }
  async function openUser(id) {
    const u = await guard(message, () => api.get(`/admin/users/${id}`));
    if (!u) return;
    const reload = async () => {
      await openUser(id);
      await load();
    };
    const buttons = [];
    if (canChange && u.is_active && !u.staff_role) buttons.push(actionButton("Lock this account", () => change(`Lock ${u.email} and end every session they have open?`, (reason) => api.post(`/admin/users/${id}/disable`, { reason }), "The account is locked.", reload), true));
    if (canChange && !u.is_active) buttons.push(actionButton("Unlock this account", () => change(`Let ${u.email} log in again?`, (reason) => api.post(`/admin/users/${id}/enable`, { reason }), "The account is unlocked.", reload)));
    detail.replaceChildren(
      el("div", { class: "kpi stack" },
        el("div", { class: "kpi-name" }, u.full_name, " ", badge(u.is_active ? "Active" : "Locked"), u.staff_role ? badge(`${u.staff_role} staff`) : null),
        el("p", {}, `${u.email} (${u.email_verified ? "verified" : "not verified"}). Joined ${ukDate(u.created_at)}. ${u.active_sessions} open session${u.active_sessions === 1 ? "" : "s"}.`),
        buttons.length ? el("div", { class: "actions" }, ...buttons) : null,
      ),
      el("h3", {}, "Businesses"),
      table(["Business", "Role", "Membership", "Business status"], u.memberships.map((m) => [m.organization_name, m.role, m.status, m.organization_status]), { empty: "Not in any business." }),
    );
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const go = el("button", { type: "button" }, "Search");
  go.addEventListener("click", () => load());
  search.addEventListener("keydown", (event) => event.key === "Enter" && load());
  panel.replaceChildren(el("div", { class: "actions" }, search, go), list, detail);
  await load();
}

// --- audit log ---------------------------------------------------------------------------------------------------

async function showAudit() {
  const who = input("actor_email", { type: "text", placeholder: "Done by (email)", "aria-label": "Done by" });
  const what = input("action", { type: "text", placeholder: "Action, e.g. admin.user_disabled", "aria-label": "Action" });
  const org = input("organization_id", { type: "text", placeholder: "Business id", "aria-label": "Business id" });
  const rows = el("div", { class: "stack" });
  const older = el("button", { type: "button", class: "secondary" }, "Older entries");
  let cursor = null;
  const entries = [];
  async function load(more = false) {
    const params = new URLSearchParams({ limit: 50 });
    if (who.value.trim()) params.set("actor_email", who.value.trim());
    if (what.value.trim()) params.set("action", what.value.trim());
    if (org.value.trim()) params.set("organization_id", org.value.trim());
    if (more && cursor) params.set("before", cursor);
    const page = await guard(message, () => api.get(`/admin/audit-log?${params}`));
    if (!page) return;
    if (!more) entries.length = 0;
    entries.push(...page.entries);
    cursor = page.next_before;
    older.hidden = !cursor;
    rows.replaceChildren(
      table(
        ["When", "What", "Who", "Business", "About", "Details"],
        entries.map((e) => [ukDateTime(e.created_at), e.action, e.actor_email ?? "system", e.organization_name ?? "", e.target_type ? `${e.target_type} ${e.target_id ?? ""}` : "", e.details ? JSON.stringify(e.details) : ""]),
        { empty: "Nothing recorded matches." },
      ),
    );
  }
  const go = el("button", { type: "button" }, "Show");
  go.addEventListener("click", () => load());
  older.addEventListener("click", () => load(true));
  panel.replaceChildren(el("div", { class: "actions" }, who, what, org, go), rows, older);
  await load();
}

// --- plans ---------------------------------------------------------------------------------------------------------

const pounds = (pence) => (pence == null ? "" : (pence / 100).toFixed(2));

async function showPlans() {
  const plans = await guard(message, () => api.get("/admin/plans"));
  if (!plans) return;
  const reload = showPlans;
  panel.replaceChildren(
    el("p", { class: "muted" }, "Prices are before VAT. A change to a price applies to businesses choosing a plan from now on; nobody already paying is charged differently. Every price and limit here began as a placeholder to confirm."),
    el("div", { class: "kpi-grid" }, ...plans.map((p) => planCard(p, reload))),
  );
}

function planCard(plan, reload) {
  const month = input("month", { type: "number", min: 0, step: "0.01", value: pounds(plan.price_month_pence), "aria-label": `${plan.name} monthly price in pounds` });
  const year = input("year", { type: "number", min: 0, step: "0.01", value: pounds(plan.price_year_pence), "aria-label": `${plan.name} yearly price in pounds` });
  const body = [
    el("div", { class: "kpi-name" }, plan.name, " ", badge(plan.is_active ? (plan.is_public ? "Shown" : "Hidden") : "Retired"), plan.self_serve ? null : badge("By arrangement")),
    canChange && plan.self_serve ? el("div", { class: "stack" }, field("Per month (£)", month), field("Per year (£)", year)) : el("p", {}, plan.price_month_pence == null ? "No price" : `£${pounds(plan.price_month_pence)} a month, £${pounds(plan.price_year_pence)} a year`),
    el("ul", {}, ...plan.features.map((f) => el("li", {}, `${FEATURE_LABEL[f.feature]}: ${!f.enabled ? "not included" : f.limit == null ? "no limit" : `up to ${f.limit}`}`))),
  ];
  if (canChange) {
    const buttons = [];
    if (plan.self_serve) {
      buttons.push(actionButton("Save prices", () => {
        const m = Math.round(Number(month.value) * 100);
        const y = Math.round(Number(year.value) * 100);
        if (!Number.isFinite(m) || !Number.isFinite(y) || m < 0 || y < 0) return showMessage(message, "error", "Enter the prices in pounds.");
        return change(`Change the ${plan.name} prices to £${pounds(m)} a month and £${pounds(y)} a year?`, (reason) => api.patch(`/admin/plans/${plan.code}`, { price_month_pence: m, price_year_pence: y, reason }), "The prices are saved.", reload);
      }));
    }
    buttons.push(actionButton(plan.is_public ? "Hide from businesses" : "Show to businesses", () => change(`${plan.is_public ? "Hide" : "Show"} the ${plan.name} plan?`, (reason) => api.patch(`/admin/plans/${plan.code}`, { is_public: !plan.is_public, reason }), "Saved.", reload)));
    body.push(el("div", { class: "actions" }, ...buttons), featureEditor(plan, reload));
  }
  return el("div", { class: "kpi stack" }, ...body);
}

function featureEditor(plan, reload) {
  const rows = plan.features.map((f) => {
    const on = el("input", { type: "checkbox", "aria-label": `${FEATURE_LABEL[f.feature]} included in ${plan.name}` });
    on.checked = f.enabled;
    const limit = input("limit", { type: "number", min: 0, value: f.limit ?? "", placeholder: "no limit", "aria-label": `${FEATURE_LABEL[f.feature]} limit` });
    limit.hidden = f.feature === "ai_assistant";
    const save = el("button", { type: "button", class: "secondary" }, "Save");
    save.addEventListener("click", () => {
      const value = limit.value === "" || limit.hidden ? null : Number(limit.value);
      if (value !== null && (!Number.isInteger(value) || value < 0)) return showMessage(message, "error", "A limit is a whole number, or empty for no limit.");
      return change(`Change what the ${plan.name} plan includes of ${FEATURE_LABEL[f.feature].toLowerCase()}?`, (reason) => api.put(`/admin/plans/${plan.code}/features/${f.feature}`, { enabled: on.checked, limit: value, reason }), "Saved.", reload);
    });
    return el("div", { class: "actions" }, el("label", {}, on, ` ${FEATURE_LABEL[f.feature]}`), limit, save);
  });
  return el("details", {}, el("summary", {}, "What it includes"), ...rows);
}

// --- the page -------------------------------------------------------------------------------------------------------

const SHOW = { organizations: showOrganizations, users: showUsers, audit: showAudit, plans: showPlans };

function drawTabs() {
  tabBar.replaceChildren(
    ...TABS.map(([key, label]) => {
      const button = el("button", { type: "button", role: "tab", "aria-selected": key === tab ? "true" : "false" }, label);
      button.addEventListener("click", () => {
        location.hash = key;
      });
      return button;
    }),
  );
}

async function show() {
  const wanted = location.hash.slice(1);
  tab = SHOW[wanted] ? wanted : "organizations";
  drawTabs();
  showMessage(message, "info", "");
  await SHOW[tab]();
}

async function start() {
  let me;
  try {
    me = await api.get("/admin/me");
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
    return;
  }
  if (!me.is_staff) {
    content.replaceChildren(el("p", {}, "This page is for Vyterlix staff."), el("a", { href: "app.html" }, "Back to your businesses"));
    return;
  }
  canChange = me.role === "admin";
  document.getElementById("staff-role").textContent = me.role === "admin" ? "Admin" : "Support (look only)";
  content.replaceChildren(tabBar, panel);
  window.addEventListener("hashchange", show);
  await show();
}

// Start last, so every const above has been set up before the page first runs.
await start();
