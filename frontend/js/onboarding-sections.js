// The forms for each onboarding section. The wizard shows them one after another;
// the business page links back to them to edit later. Each render() fills `panel` and
// calls `done()` after a successful save (the wizard then moves on).

import { ApiError } from "./api.js";
import { api } from "./auth.js";
import { el } from "./dom.js";
import { MONTHS, UK_REGIONS, amount, ukDate } from "./format.js";
import { checkbox, field, input, intOrNull, orNull, select } from "./forms.js";
import { bindForm, showMessage } from "./ui.js";

const SIZES = [
  ["micro", "Micro (under 10 people)"],
  ["small", "Small (10–49 people)"],
  ["medium", "Medium (50–249 people)"],
  ["large", "Large (250+ people)"],
];
const MODELS = [
  ["b2c", "Selling to consumers"],
  ["b2b", "Selling to businesses"],
  ["b2b_and_b2c", "Both consumers and businesses"],
  ["marketplace", "A marketplace"],
  ["subscription", "Subscriptions"],
];
const GOAL_TYPES = [
  ["increase_revenue", "Increase revenue"],
  ["improve_margin", "Improve profit margin"],
  ["reduce_costs", "Reduce costs"],
  ["improve_cash_flow", "Improve cash flow"],
  ["grow_customers", "Grow customer numbers"],
  ["improve_retention", "Keep more customers"],
  ["reduce_stock_problems", "Fewer stock problems"],
  ["other", "Something else"],
];
const UNITS = [["gbp", "£"], ["percent", "%"], ["count", "number"]];
const ROLES = [["viewer", "Viewer (can see everything)"], ["manager", "Manager"], ["owner", "Owner"]];
const MONTH_OPTIONS = MONTHS.map((name, i) => [i + 1, name]);
const DAY_OPTIONS = Array.from({ length: 31 }, (_, i) => [i + 1, String(i + 1)]);

const LIST_KINDS = {
  offerings: { kind: "offering", noun: "product or service", example: "e.g. Bread, Catering" },
  sales_channels: { kind: "sales_channel", noun: "channel", example: "e.g. Farmers' markets" },
  customer_types: { kind: "customer_type", noun: "customer type", example: "e.g. Schools" },
  cost_categories: { kind: "cost_category", noun: "cost", example: "e.g. Packaging" },
};

let cache = {};

async function cached(key, path) {
  cache[key] ??= api.get(path, { auth: false });
  return cache[key];
}

async function getProfile(orgId) {
  try {
    return await api.get(`/organizations/${orgId}/profile`);
  } catch (err) {
    if (err instanceof ApiError && err.code === "profile_not_set_up") return null;
    throw err;
  }
}

function messageBox() {
  return el("div", { class: "message", role: "alert", hidden: true });
}

function list(items, empty) {
  return items.length
    ? el("ul", { class: "item-list" }, ...items.map((i) => el("li", {}, ...[i].flat())))
    : el("p", { class: "muted" }, empty);
}

// --- sections -------------------------------------------------------------------------------

async function businessDetails({ panel, orgId, done }) {
  const [industries, profile] = await Promise.all([cached("industries", "/industries"), getProfile(orgId)]);
  const p = profile ?? {};
  const fy = p.financial_year_start ?? { month: 4, day: 1 };
  const box = messageBox();
  const vat = checkbox("vat_registered", "VAT registered", { checked: p.vat_registered });

  const form = el(
    "form",
    { novalidate: true },
    field("Industry", select("industry_code", industries.map((i) => [i.code, i.label]), { selected: p.industry?.code, placeholder: "Choose your industry", required: true })),
    el(
      "fieldset",
      {},
      el("legend", {}, "Financial year starts on"),
      el(
        "div",
        { class: "row" },
        field("Day", select("fy_day", DAY_OPTIONS, { selected: fy.day })),
        field("Month", select("fy_month", MONTH_OPTIONS, { selected: fy.month })),
      ),
      el("p", { class: "hint" }, "Many UK businesses use 1 April. The tax year starts on 6 April."),
    ),
    field("UK SIC code (optional)", input("sic_code", { value: p.sic_code ?? "", inputmode: "numeric", maxlength: 5 }), "5 digits, from Companies House, e.g. 47110"),
    field("Size", select("business_size", SIZES, { selected: p.business_size, placeholder: "Not sure" })),
    field("How you sell", select("business_model", MODELS, { selected: p.business_model, placeholder: "Not sure" })),
    el(
      "div",
      { class: "row" },
      field("Team size", input("team_size", { type: "number", min: 0, value: p.team_size ?? "" })),
      field("Year founded", input("founded_year", { type: "number", min: 1800, value: p.founded_year ?? "" })),
    ),
    vat,
    field("VAT number (optional)", input("vat_number", { value: p.vat_number ?? "" }), "e.g. GB 123 4567 89"),
    el("button", { type: "submit" }, "Save and continue"),
  );
  panel.append(box, form);

  bindForm(form, box, async (v) => {
    const body = {
      industry_code: v.industry_code,
      financial_year_start: { month: Number(v.fy_month), day: Number(v.fy_day) },
      sic_code: orNull(v.sic_code),
      business_size: orNull(v.business_size),
      business_model: orNull(v.business_model),
      team_size: intOrNull(v.team_size),
      founded_year: intOrNull(v.founded_year),
      vat_registered: form.elements.vat_registered.checked || null,
      vat_number: orNull(v.vat_number),
    };
    if (!body.industry_code) throw new ApiError(422, "missing", "Choose your industry to continue.");
    if (profile) {
      await api.patch(`/organizations/${orgId}/profile`, body);
    } else {
      const created = Object.fromEntries(Object.entries(body).filter(([, x]) => x !== null));
      await api.put(`/organizations/${orgId}/profile`, created);
    }
    await done();
  });
}

async function location({ panel, orgId, done }) {
  const profile = await getProfile(orgId);
  if (!profile) {
    panel.append(el("p", {}, "Add your business details first."));
    return;
  }
  const box = messageBox();
  const form = el(
    "form",
    { novalidate: true },
    field("UK region", select("region", UK_REGIONS, { selected: profile.region, placeholder: "Choose a region" })),
    field("Town or city", input("town_city", { value: profile.town_city ?? "", autocomplete: "address-level2" })),
    field("Postcode", input("postcode", { value: profile.postcode ?? "", autocomplete: "postal-code" }), "e.g. SW1A 1AA"),
    el("button", { type: "submit" }, "Save and continue"),
  );
  panel.append(box, form);
  bindForm(form, box, async (v) => {
    await api.patch(`/organizations/${orgId}/profile`, {
      region: orNull(v.region),
      town_city: orNull(v.town_city),
      postcode: orNull(v.postcode),
    });
    await done();
  });
}

async function goals({ panel, orgId, done, rerender }) {
  const existing = await api.get(`/organizations/${orgId}/goals?status=active`);
  const box = messageBox();
  const typeLabel = Object.fromEntries(GOAL_TYPES);
  panel.append(
    list(
      existing.map((g) => [
        el("strong", {}, g.title),
        el("span", { class: "muted" }, ` · ${typeLabel[g.goal_type] ?? g.goal_type}`),
        g.target_value ? el("span", {}, ` · target ${amount(g.target_value, g.target_unit)}`) : null,
        g.target_date ? el("span", {}, ` by ${ukDate(g.target_date)}`) : null,
      ]),
      "No goals yet.",
    ),
  );
  const form = el(
    "form",
    { novalidate: true, class: "subform" },
    el("h3", {}, "Add a goal"),
    field("Goal", input("title", { placeholder: "e.g. Reach £250,000 turnover" })),
    field("Type", select("goal_type", GOAL_TYPES, { selected: "increase_revenue" })),
    el(
      "div",
      { class: "row" },
      field("Target (optional)", input("target_value", { type: "number", step: "0.01", min: 0 })),
      field("Unit", select("target_unit", UNITS, { selected: "gbp" })),
      field("By (optional)", input("target_date", { type: "date" })),
    ),
    field("Priority", select("priority", [[1, "1 – highest"], [2, "2"], [3, "3"], [4, "4"], [5, "5 – lowest"]], { selected: 3 })),
    el("button", { type: "submit", class: "secondary" }, "Add goal"),
  );
  panel.append(box, form, el("button", { type: "button", onclick: done }, "Continue"));
  bindForm(form, box, async (v) => {
    const target = orNull(v.target_value);
    await api.post(`/organizations/${orgId}/goals`, {
      title: v.title,
      goal_type: v.goal_type,
      target_value: target,
      target_unit: target ? v.target_unit : null,
      target_date: orNull(v.target_date),
      priority: Number(v.priority),
    });
    await rerender();
  });
}

function listSection(key) {
  const { kind, noun, example } = LIST_KINDS[key];
  return async ({ panel, orgId, done, rerender }) => {
    const [items, suggestions] = await Promise.all([
      api.get(`/organizations/${orgId}/lists/${kind}`),
      cached("suggestions", "/business-list-suggestions"),
    ]);
    const have = new Set(items.map((i) => i.name.toLowerCase()));
    const offered = (suggestions[kind] ?? []).filter((s) => !have.has(s.toLowerCase()));
    const box = messageBox();

    panel.append(
      list(
        items.map((i) => [
          i.name,
          i.is_cost_of_sales ? el("span", { class: "badge" }, "cost of sales") : null,
        ]),
        `Nothing added yet.`,
      ),
    );
    const form = el(
      "form",
      { novalidate: true, class: "subform" },
      offered.length ? el("p", {}, "Tick any that apply:") : null,
      offered.length ? el("div", { class: "checks" }, ...offered.map((s) => checkbox("pick", s, { value: s }))) : null,
      field(`Add your own ${noun}`, input("custom", { placeholder: example })),
      kind === "cost_category"
        ? checkbox("custom_cogs", "It's a cost of sales (goes up and down with what you sell)")
        : null,
      el("button", { type: "submit", class: "secondary" }, "Add"),
    );
    panel.append(box, form, el("button", { type: "button", onclick: done }, "Continue"));

    bindForm(form, box, async () => {
      const picked = [...form.querySelectorAll("input[name=pick]:checked")].map((b) => b.value);
      const custom = orNull(form.elements.custom.value);
      if (!picked.length && !custom) throw new ApiError(422, "empty", `Tick or type a ${noun} to add.`);
      if (picked.length) await api.post(`/organizations/${orgId}/lists/${kind}/bulk`, { names: picked });
      if (custom) {
        const body = { name: custom };
        if (kind === "cost_category") body.is_cost_of_sales = form.elements.custom_cogs.checked;
        await api.post(`/organizations/${orgId}/lists/${kind}`, body);
      }
      await rerender();
    });
  };
}

async function seasons({ panel, orgId, done, rerender }) {
  const existing = await api.get(`/organizations/${orgId}/seasons?status=active`);
  const box = messageBox();
  panel.append(
    list(
      existing.map((s) => [
        el("strong", {}, s.name),
        ` · ${s.label}`,
        s.expected_change_pct !== null
          ? el("span", { class: "muted" }, ` · ${Number(s.expected_change_pct) > 0 ? "+" : ""}${amount(s.expected_change_pct, "percent")}`)
          : null,
      ]),
      "No busy or quiet times added.",
    ),
  );
  const form = el(
    "form",
    { novalidate: true, class: "subform" },
    el("h3", {}, "Add a busy or quiet time"),
    field("Name", input("name", { placeholder: "e.g. Christmas rush" })),
    el(
      "div",
      { class: "row" },
      field("From day", select("start_day", DAY_OPTIONS, { selected: 1 })),
      field("From month", select("start_month", MONTH_OPTIONS, { selected: 12 })),
      field("To day", select("end_day", DAY_OPTIONS, { selected: 31 })),
      field("To month", select("end_month", MONTH_OPTIONS, { selected: 12 })),
    ),
    field("Usual change (%)", input("expected_change_pct", { type: "number", step: "1" }), "+40 = 40% busier than normal, -20 = 20% quieter"),
    el("button", { type: "submit", class: "secondary" }, "Add"),
  );
  panel.append(box, form, el("button", { type: "button", onclick: done }, "Continue"));
  bindForm(form, box, async (v) => {
    await api.post(`/organizations/${orgId}/seasons`, {
      name: v.name,
      start: { month: Number(v.start_month), day: Number(v.start_day) },
      end: { month: Number(v.end_month), day: Number(v.end_day) },
      expected_change_pct: orNull(v.expected_change_pct),
    });
    await rerender();
  });
}

async function team({ panel, orgId, done, rerender }) {
  const [members, invitations] = await Promise.all([
    api.get(`/organizations/${orgId}/members`),
    api.get(`/organizations/${orgId}/invitations`),
  ]);
  const pending = invitations.filter((i) => i.status === "pending");
  const box = messageBox();
  panel.append(
    list(
      [
        ...members.map((m) => [m.full_name, el("span", { class: "badge" }, m.role)]),
        ...pending.map((i) => [i.email, el("span", { class: "badge" }, `invited · ${i.role}`)]),
      ],
      "Just you so far.",
    ),
  );
  const form = el(
    "form",
    { novalidate: true, class: "subform" },
    el("h3", {}, "Invite someone"),
    field("Email", input("email", { type: "email", autocomplete: "off" })),
    field("Role", select("role", ROLES, { selected: "viewer" })),
    el("button", { type: "submit", class: "secondary" }, "Send invitation"),
  );
  panel.append(box, form, el("button", { type: "button", onclick: done }, "Continue"));
  bindForm(form, box, async (v) => {
    await api.post(`/organizations/${orgId}/invitations`, { email: v.email.trim(), role: v.role });
    showMessage(box, "success", `Invitation sent to ${v.email.trim()}.`);
    await rerender();
  });
}

export const SECTION_RENDERERS = {
  business_details: businessDetails,
  location,
  goals,
  offerings: listSection("offerings"),
  sales_channels: listSection("sales_channels"),
  customer_types: listSection("customer_types"),
  cost_categories: listSection("cost_categories"),
  seasons,
  team,
};

export function clearCache() {
  cache = {};
}
