import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { el } from "../dom.js";
import { WEEKDAYS, regionName } from "../format.js";
import { checkbox, field, input, select } from "../forms.js";
import { bindForm, showMessage } from "../ui.js";

const message = document.getElementById("message");
const STATUS_LABEL = { done: "Done", to_do: "To do", skipped: "Skipped" };
const CATEGORY_LABEL = {
  sales: "Sales",
  financial: "Money and cash flow",
  customer: "Customers",
  inventory: "Stock",
  marketing: "Marketing",
  forecast: "Forecasts",
  action: "Actions and follow-ups",
  data: "Data and imports",
  security: "Security",
};

const opened = await openBusiness(message);
if (opened) await start(opened);

async function start({ org }) {
  const orgId = org.id;
  const isOwner = org.role === "owner";
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("org-role").textContent = org.role;

  const [onboarding, profile] = await Promise.all([
    api.get(`/organizations/${orgId}/onboarding`),
    api.get(`/organizations/${orgId}/profile`).catch((err) => {
      if (err instanceof ApiError && err.code === "profile_not_set_up") return null;
      throw err;
    }),
  ]);
  renderSetup(orgId, isOwner, onboarding, profile);
  await renderSettings(orgId, isOwner);
  await renderNotifications(orgId);
}

function renderSetup(orgId, isOwner, state, profile) {
  const card = document.getElementById("setup");
  const summary = profile
    ? [profile.industry.label, [profile.town_city, regionName(profile.region)].filter(Boolean).join(", ")]
        .filter(Boolean)
        .join(" · ")
    : "Not set up yet";
  card.replaceChildren(
    el("h2", {}, "Business setup"),
    el("p", { class: "muted" }, summary),
    el("p", {}, `${state.done} of ${state.total} sections done`),
    el(
      "ul",
      { class: "item-list" },
      ...state.sections.map((s) =>
        el(
          "li",
          {},
          el("span", {}, s.title),
          el(
            "span",
            {},
            el("span", { class: `badge status-${s.status}` }, STATUS_LABEL[s.status]),
            isOwner ? " " : null,
            isOwner
              ? el("a", { href: `onboarding.html?org=${orgId}&section=${s.key}` }, "Edit")
              : null,
          ),
        ),
      ),
    ),
    // replaceChildren() would print a null as the text "null", so only add it when needed.
    ...(!state.ready_for_dashboard && isOwner
      ? [el("a", { class: "button", href: `onboarding.html?org=${orgId}` }, "Continue setting up")]
      : []),
  );
}

async function renderSettings(orgId, isOwner) {
  const card = document.getElementById("settings");
  const s = await api.get(`/organizations/${orgId}/settings`);
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const quiet = checkbox("quiet_on", "Hold back non-urgent alerts during quiet hours", {
    checked: Boolean(s.quiet_hours),
  });
  const form = el(
    "form",
    { novalidate: true },
    el(
      "p",
      { class: "muted" },
      `Times are UK time (${s.timezone}); dates show as ${s.date_format}; money in ${s.currency}.`,
    ),
    field(
      "Weeks start on",
      select("week_start_day", WEEKDAYS.map((d, i) => [i + 1, d]), { selected: s.week_start_day }),
    ),
    quiet,
    el(
      "div",
      { class: "row" },
      field("From", input("quiet_start", { type: "time", value: s.quiet_hours?.start.slice(0, 5) ?? "22:00" })),
      field("Until", input("quiet_end", { type: "time", value: s.quiet_hours?.end.slice(0, 5) ?? "07:00" })),
    ),
    isOwner ? el("button", { type: "submit" }, "Save settings") : el("p", { class: "muted" }, "Only an owner can change these."),
  );
  if (!isOwner) for (const control of form.elements) control.disabled = true;
  card.replaceChildren(el("h2", {}, "Business settings"), box, form);

  bindForm(form, box, async (v) => {
    await api.patch(`/organizations/${orgId}/settings`, {
      week_start_day: Number(v.week_start_day),
      quiet_hours: form.elements.quiet_on.checked ? { start: v.quiet_start, end: v.quiet_end } : null,
    });
    showMessage(box, "success", "Settings saved.");
  });
}

async function renderNotifications(orgId) {
  const card = document.getElementById("notifications");
  const prefs = await api.get(`/organizations/${orgId}/notification-preferences`);
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const channels = [["email", "Email"], ["in_app", "In the app"], ["push", "Phone"]];

  const rows = prefs.map((p) =>
    el(
      "tr",
      {},
      el("th", { scope: "row" }, CATEGORY_LABEL[p.category] ?? p.category),
      ...channels.map(([channel, label]) => {
        const locked = p.locked && channel !== "push";
        const tick = el("input", {
          type: "checkbox",
          "aria-label": `${CATEGORY_LABEL[p.category]}: ${label}`,
          dataset: { category: p.category, channel },
        });
        tick.checked = p[channel];
        tick.disabled = locked;
        return el("td", {}, tick);
      }),
    ),
  );
  const form = el(
    "form",
    { novalidate: true },
    el(
      "table",
      { class: "grid" },
      el("thead", {}, el("tr", {}, el("th", {}, "Alerts about"), ...channels.map(([, l]) => el("th", { scope: "col" }, l)))),
      el("tbody", {}, ...rows),
    ),
    el("p", { class: "hint" }, "Security alerts always come by email and in the app, to keep your account safe."),
    el("button", { type: "submit" }, "Save my notifications"),
  );
  card.replaceChildren(
    el("h2", {}, "My notifications"),
    el("p", { class: "muted" }, "Just for you, in this business."),
    box,
    form,
  );

  bindForm(form, box, async () => {
    const preferences = {};
    for (const input of form.querySelectorAll("input[type=checkbox]:not(:disabled)")) {
      const { category, channel } = input.dataset;
      (preferences[category] ??= {})[channel] = input.checked;
    }
    await api.patch(`/organizations/${orgId}/notification-preferences`, { preferences });
    showMessage(box, "success", "Your notification choices are saved.");
  });
}
