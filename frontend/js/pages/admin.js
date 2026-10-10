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
const TABS = [["health", "Health"], ["organizations", "Businesses"], ["users", "People"], ["cases", "Support cases"], ["flags", "Feature switches"], ["audit", "Audit log"], ["plans", "Plans"]];
const FEATURE_LABEL = { members: "Team members", integrations: "Connections", scheduled_reports: "Scheduled reports", ai_assistant: "AI assistant" };

let canChange = false;
let tab = "health";
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
        el("p", {}, `Using ${o.usage.members} of the places for people, ${o.usage.integrations} connections and ${o.usage.scheduled_reports} scheduled reports. ${o.open_cases} open support case${o.open_cases === 1 ? "" : "s"}.`),
        buttons.length ? el("div", { class: "actions" }, ...buttons) : null,
      ),
      noteBox({ organization_id: id }, o.notes, reload),
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
      noteBox({ user_id: id }, u.notes, reload),
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


// --- notes ------------------------------------------------------------------------------------------------------------

/** Staff-only notes about a business or a person, and a box to add one. `subject` is { organization_id } or { user_id }. */
function noteBox(subject, notes, reload) {
  const box = el("textarea", { name: "note", rows: 2, maxlength: 4000, "aria-label": "New note" });
  const add = el("button", { type: "button", class: "secondary" }, "Add note");
  add.addEventListener("click", async () => {
    if (!box.value.trim()) return;
    add.disabled = true;
    try {
      const ok = await guard(message, () => api.post("/admin/notes", { ...subject, body: box.value.trim() }));
      if (ok) await reload();
    } finally {
      add.disabled = false;
    }
  });
  return el("div", { class: "kpi stack" },
    el("div", { class: "kpi-name" }, "Staff notes (never shown to the business)"),
    ...notes.map((n) => el("p", {}, el("span", { class: "muted" }, `${ukDateTime(n.created_at)}, ${n.author_email ?? "someone"}: `), n.body)),
    notes.length ? null : el("p", { class: "muted" }, "No notes yet."),
    box,
    el("div", { class: "actions" }, add),
  );
}

// --- health -----------------------------------------------------------------------------------------------------------

function stat(label, value, note) {
  return el("div", { class: "kpi" }, el("div", { class: "kpi-name" }, label), el("div", {}, String(value)), note ? el("div", { class: "muted" }, note) : null);
}

async function showHealth() {
  const [h, events] = await Promise.all([guard(message, () => api.get("/admin/health")), guard(message, () => api.get("/admin/events?open_only=true&limit=50"))]);
  if (!h || !events) return;
  const waiting = h.jobs.oldest_waiting_seconds == null ? "" : `oldest has waited ${h.jobs.oldest_waiting_seconds} seconds`;
  const rows = events.items.map((e) => {
    const done = el("button", { type: "button", class: "secondary" }, "Dealt with");
    done.addEventListener("click", async () => {
      done.disabled = true;
      try {
        const ok = await guard(message, () => api.post(`/admin/events/${e.id}/resolve`));
        if (ok !== undefined) await showHealth();
      } finally {
        done.disabled = false;
      }
    });
    return [ukDateTime(e.created_at), badge(e.severity), e.message, e.organization_name ?? "", e.details ? JSON.stringify(e.details) : "", done];
  });
  panel.replaceChildren(
    el("div", { class: `message message-${h.status === "ok" ? "success" : "error"}` }, h.status === "ok" ? "Everything looks fine." : "Something needs attention."),
    el("div", { class: "kpi-grid" },
      stat("Database", h.database_ok ? "Working" : "NOT WORKING", h.migration ? `version ${h.migration}` : ""),
      stat("Background work waiting", h.jobs.queued, waiting),
      stat("Running", h.jobs.running, h.jobs.stuck ? `${h.jobs.stuck} not heard from for 5 minutes` : ""),
      stat("Failed in the last day", h.jobs.failed_last_day),
      stat("Emails waiting to go", h.emails.waiting),
      stat("Emails that failed (last day)", h.emails.failed_last_day),
      stat("Connections", h.integrations.connected, `${h.integrations.needing_attention} need attention`),
      stat("People", h.accounts.people, `${h.accounts.locked_people} locked`),
      stat("Businesses", Object.entries(h.accounts.businesses).map(([k, v]) => `${v} ${k}`).join(", ") || "none"),
      stat("Plans", Object.entries(h.accounts.subscriptions).map(([k, v]) => `${v} ${k}`).join(", ") || "none yet"),
      stat("Open support cases", h.open_cases),
    ),
    el("p", { class: "muted" }, `Environment: ${h.environment}. Email: ${h.email_backend}. AI: ${h.ai_provider}. Checked ${ukDateTime(h.checked_at)}.`),
    el("h3", {}, `Things that went wrong and have not been dealt with (${h.open_errors} errors, ${h.open_warnings} warnings)`),
    table(["When", "How bad", "What", "Business", "Details", ""], rows, { empty: "Nothing outstanding." }),
  );
}

// --- support cases --------------------------------------------------------------------------------------------------------

async function showCases() {
  const status = select("status", [["", "Any status"], ["open", "Open"], ["waiting", "Waiting"], ["resolved", "Resolved"]], { "aria-label": "Status" });
  const mine = el("input", { type: "checkbox", "aria-label": "Only mine" });
  const list = el("div", { class: "stack" });
  const detail = el("div", { class: "stack" });
  const orgs = (await guard(message, () => api.get("/admin/organizations?limit=100")))?.items ?? [];
  const limit = 25;

  async function load(offset = 0) {
    const params = new URLSearchParams({ limit, offset });
    if (status.value) params.set("status", status.value);
    if (mine.checked) params.set("mine", "true");
    const page = await guard(message, () => api.get(`/admin/cases?${params}`));
    if (!page) return;
    page.offset = offset;
    list.replaceChildren(
      table(
        ["Case", "Status", "Priority", "Business", "Assigned to", "Opened", ""],
        page.items.map((c) => {
          const open = el("button", { type: "button", class: "link" }, "Open");
          open.addEventListener("click", () => openCase(c.id));
          return [c.subject, badge(c.status), c.priority, c.organization_name ?? "", c.assigned_to_email ?? "nobody", ukDate(c.created_at), open];
        }),
        { empty: "No cases match." },
      ),
      pager(page, limit, load),
    );
  }

  async function openCase(id) {
    const c = await guard(message, () => api.get(`/admin/cases/${id}`));
    if (!c) return;
    const reload = async () => {
      await openCase(id);
      await load();
    };
    const state = select("status", [["open", "Open"], ["waiting", "Waiting"], ["resolved", "Resolved"]], { selected: c.status, "aria-label": "Status" });
    const priority = select("priority", [["low", "Low"], ["normal", "Normal"], ["high", "High"]], { selected: c.priority, "aria-label": "Priority" });
    const who = input("assignee", { type: "text", value: c.assigned_to_email ?? "", placeholder: "Give to (staff email)", "aria-label": "Give to" });
    const save = el("button", { type: "button" }, "Save changes");
    save.addEventListener("click", async () => {
      const changes = { status: state.value, priority: priority.value };
      if (who.value.trim() && who.value.trim().toLowerCase() !== (c.assigned_to_email ?? "")) changes.assigned_to_email = who.value.trim();
      if (!who.value.trim() && c.assigned_to_email) changes.unassign = true;
      save.disabled = true;
      try {
        const ok = await guard(message, () => api.patch(`/admin/cases/${id}`, changes));
        if (ok) {
          showMessage(message, "success", "Saved.");
          await reload();
        }
      } finally {
        save.disabled = false;
      }
    });
    const note = el("textarea", { name: "note", rows: 3, maxlength: 4000, "aria-label": "New note" });
    const addNote = el("button", { type: "button", class: "secondary" }, "Add note");
    addNote.addEventListener("click", async () => {
      if (!note.value.trim()) return;
      addNote.disabled = true;
      try {
        const ok = await guard(message, () => api.post(`/admin/cases/${id}/notes`, { body: note.value.trim() }));
        if (ok) await reload();
      } finally {
        addNote.disabled = false;
      }
    });
    detail.replaceChildren(
      el("div", { class: "kpi stack" },
        el("div", { class: "kpi-name" }, c.subject, " ", badge(c.status)),
        el("p", { class: "muted" }, `Opened ${ukDateTime(c.created_at)}${c.created_by_email ? ` by ${c.created_by_email}` : ""}${c.organization_name ? ` about ${c.organization_name}` : ""}${c.requester_email ? `, asked by ${c.requester_email}` : ""}.${c.resolved_at ? ` Resolved ${ukDateTime(c.resolved_at)}.` : ""}`),
        el("div", { class: "actions" }, state, priority, who, save),
      ),
      el("div", { class: "kpi stack" },
        el("div", { class: "kpi-name" }, "Notes"),
        ...c.notes.map((n) => el("p", {}, el("span", { class: "muted" }, `${ukDateTime(n.created_at)}, ${n.author_email ?? "someone"}: `), n.body)),
        c.notes.length ? null : el("p", { class: "muted" }, "No notes yet."),
        note,
        el("div", { class: "actions" }, addNote),
      ),
    );
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const subject = input("subject", { type: "text", placeholder: "What is it about?", maxlength: 200, "aria-label": "Subject" });
  const about = select("organization_id", orgs.map((o) => [o.id, o.name]), { placeholder: "A business (optional)", "aria-label": "Business" });
  const urgency = select("priority", [["low", "Low"], ["normal", "Normal"], ["high", "High"]], { selected: "normal", "aria-label": "Priority" });
  const first = el("textarea", { name: "first_note", rows: 2, maxlength: 4000, placeholder: "First note (optional)", "aria-label": "First note" });
  const create = el("button", { type: "button" }, "Open a case");
  create.addEventListener("click", async () => {
    if (subject.value.trim().length < 3) return showMessage(message, "error", "Give the case a subject of at least three letters.");
    create.disabled = true;
    try {
      const body = { subject: subject.value.trim(), priority: urgency.value };
      if (about.value) body.organization_id = about.value;
      if (first.value.trim()) body.note = first.value.trim();
      const made = await guard(message, () => api.post("/admin/cases", body));
      if (made) {
        subject.value = first.value = "";
        showMessage(message, "success", "The case is open.");
        await load();
        await openCase(made.id);
      }
    } finally {
      create.disabled = false;
    }
  });
  const go = el("button", { type: "button" }, "Show");
  go.addEventListener("click", () => load());
  panel.replaceChildren(
    el("div", { class: "kpi stack" }, el("div", { class: "kpi-name" }, "New case"), el("div", { class: "actions" }, subject, about, urgency), first, el("div", { class: "actions" }, create)),
    el("div", { class: "actions" }, status, el("label", {}, mine, " Only mine"), go),
    list,
    detail,
  );
  await load();
}

// --- feature switches ---------------------------------------------------------------------------------------------------------

async function showFlags() {
  const flags = await guard(message, () => api.get("/admin/flags"));
  if (!flags) return;
  const orgs = canChange ? ((await guard(message, () => api.get("/admin/organizations?limit=100")))?.items ?? []) : [];
  const children = [el("p", { class: "muted" }, "A feature switch turns something on for everyone, or only for the businesses you choose. A business's own entry wins over the general setting.")];
  if (canChange) children.push(newFlagForm());
  children.push(...(flags.length ? flags.map((f) => flagCard(f, orgs)) : [el("p", { class: "muted" }, "No switches yet.")]));
  panel.replaceChildren(...children);
}

function newFlagForm() {
  const key = input("key", { type: "text", placeholder: "name_with_underscores", maxlength: 41, "aria-label": "Switch name" });
  const description = input("description", { type: "text", placeholder: "What it switches", maxlength: 300, "aria-label": "Description" });
  const on = el("input", { type: "checkbox", "aria-label": "On for everyone to begin with" });
  const create = el("button", { type: "button" }, "Create switch");
  create.addEventListener("click", () => change(`Create the switch ${key.value.trim()}?`, (reason) => api.post("/admin/flags", { key: key.value.trim(), description: description.value.trim(), enabled: on.checked, reason }), "The switch is created.", showFlags));
  return el("div", { class: "kpi stack" }, el("div", { class: "kpi-name" }, "New switch"), el("div", { class: "actions" }, key, description, el("label", {}, on, " On for everyone"), create));
}

function flagCard(flag, orgs) {
  const kids = [
    el("div", { class: "kpi-name" }, flag.key, " ", badge(flag.enabled ? "On for everyone" : "Off unless chosen")),
    el("p", {}, flag.description),
  ];
  for (const o of flag.overrides) {
    const clear = actionButton("Remove", () => change(`Remove ${o.organization_name ?? "this business"}'s own entry for ${flag.key}?`, (reason) => api.delete(`/admin/flags/${flag.key}/organizations/${o.organization_id}?reason=${encodeURIComponent(reason)}`), "Removed.", showFlags));
    kids.push(el("div", { class: "actions" }, el("span", {}, `${o.organization_name ?? o.organization_id}: ${o.enabled ? "on" : "off"}`), canChange ? clear : null));
  }
  if (canChange) {
    const who = select("organization_id", orgs.map((o) => [o.id, o.name]), { placeholder: "Choose a business", "aria-label": "Business" });
    const how = select("enabled", [["true", "On"], ["false", "Off"]], { "aria-label": "On or off" });
    const set = actionButton("Set for this business", async () => {
      if (!who.value) return showMessage(message, "error", "Choose a business first.");
      await change(`Set ${flag.key} ${how.value === "true" ? "on" : "off"} for this business?`, (reason) => api.put(`/admin/flags/${flag.key}/organizations/${who.value}`, { enabled: how.value === "true", reason }), "Saved.", showFlags);
    });
    kids.push(
      el("div", { class: "actions" }, who, how, set),
      el("div", { class: "actions" },
        actionButton(flag.enabled ? "Turn off for everyone" : "Turn on for everyone", () => change(`${flag.enabled ? "Turn off" : "Turn on"} ${flag.key} for everyone?`, (reason) => api.patch(`/admin/flags/${flag.key}`, { enabled: !flag.enabled, reason }), "Saved.", showFlags)),
        actionButton("Delete this switch", () => change(`Delete the switch ${flag.key}?`, (reason) => api.delete(`/admin/flags/${flag.key}?reason=${encodeURIComponent(reason)}`), "Deleted.", showFlags), true),
      ),
    );
  }
  return el("div", { class: "kpi stack" }, ...kids);
}

// --- the page -------------------------------------------------------------------------------------------------------

const SHOW = { health: showHealth, organizations: showOrganizations, users: showUsers, cases: showCases, flags: showFlags, audit: showAudit, plans: showPlans };

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
  tab = SHOW[wanted] ? wanted : "health";
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
