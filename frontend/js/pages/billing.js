// Plan and billing: what your business is on, what that lets you do and how much of it you use,
// the plans you can move to, and what you have been charged. Anyone in the business can look; only
// the owner can choose, change or cancel a plan. Prices exclude VAT, which is added when you pay.
// Paying happens on the payment provider's own page: we never see or keep card details.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, table } from "../data.js";
import { el } from "../dom.js";
import { select } from "../forms.js";
import { ukDate } from "../format.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const PROVIDER_NAME = { stripe: "Card (Stripe)", paystack: "Paystack", sandbox: "Test payment (no money moves)" };

let base = "";
let canManage = false;
let interval = "month";
let sandboxToken = null;
const current = el("div", { class: "stack" });
const plansBox = el("div", { class: "stack" });
const invoicesBox = el("div", { class: "stack" });

async function start({ org }) {
  base = `/organizations/${org.id}/billing`;
  document.getElementById("org-name").textContent = org.name;
  content.replaceChildren(current, el("h2", {}, "Plans"), plansBox, el("h2", {}, "What you have been charged"), invoicesBox);
  readReturn(org.id);
  await refresh();
}

/** Coming back from paying: a test payment still has to be finished; a real one is on its way. */
function readReturn(orgId) {
  const params = new URLSearchParams(location.search);
  sandboxToken = params.get("sandbox");
  if (params.get("paid") === "1" && !sandboxToken) {
    showMessage(message, "success", "Thank you. Your payment is being confirmed. Your new plan shows here as soon as the payment provider tells us.");
  }
  if (params.get("cancelled") === "1") showMessage(message, "info", "You did not finish paying, so nothing has changed.");
  if (params.has("sandbox") || params.has("paid") || params.has("cancelled")) {
    history.replaceState(null, "", `billing.html?org=${orgId}`);
  }
}

async function refresh() {
  const [billing, plans, invoices] = await Promise.all([
    guard(message, () => api.get(base)),
    guard(message, () => api.get(`${base}/plans`)),
    api.get(`${base}/invoices`).catch((err) => {
      if (err instanceof ApiError && err.status === 403) return null;
      throw err;
    }),
  ]);
  if (!billing || !plans) return;
  canManage = billing.can_manage;
  showCurrent(billing);
  showPlans(plans, billing);
  showInvoices(invoices);
}

function usage(f) {
  if (!f.enabled) return "Not included";
  if (f.used == null) return "Included";
  return f.limit == null ? `${f.used} in use, no limit` : `${f.used} of ${f.limit} in use`;
}

function showCurrent(billing) {
  const sub = billing.subscription;
  const kids = [
    el("div", { class: "kpi stack" },
      el("div", { class: "kpi-name" }, `${sub.plan_name} plan `, el("span", { class: "badge" }, sub.status_label)),
      el("p", {}, sub.message),
      sub.scheduled_plan_name ? el("p", { class: "muted" }, `Moving to the ${sub.scheduled_plan_name} plan when this period ends.`) : null,
      sub.current_period_end ? el("p", { class: "muted" }, `Current period: ${ukDate(sub.current_period_start)} to ${ukDate(sub.current_period_end)}`) : null,
      canManage ? ownerButtons(sub) : null,
    ),
    el("div", { class: "kpi-grid" },
      ...billing.features.map((f) => el("div", { class: "kpi" }, el("div", { class: "kpi-name" }, f.label), el("div", {}, usage(f)))),
    ),
  ];
  if (sandboxToken && canManage) kids.unshift(sandboxPanel());
  current.replaceChildren(...kids);
}

function ownerButtons(sub) {
  if (sub.status !== "active" && sub.status !== "past_due") return null;
  return el("div", { class: "actions" },
    sub.cancel_at_period_end
      ? act("Keep my plan", "resume", "Your plan will carry on renewing.")
      : act("Cancel my plan", "cancel", "Your plan will end when the period you have paid for does.", true),
  );
}

function act(label, path, done, confirmFirst = false) {
  const button = el("button", { type: "button", class: confirmFirst ? "secondary" : "" }, label);
  button.addEventListener("click", async () => {
    if (confirmFirst && !confirm(`${label}? ${done}`)) return;
    button.disabled = true;
    try {
      const ok = await guard(message, () => api.post(`${base}/${path}`));
      if (ok) {
        showMessage(message, "success", done);
        await refresh();
      }
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function sandboxPanel() {
  const pay = el("button", { type: "button" }, "Complete the test payment");
  pay.addEventListener("click", async () => {
    pay.disabled = true;
    try {
      const done = await guard(message, () => api.post(`${base}/sandbox/complete`, { token: sandboxToken }));
      if (done) {
        sandboxToken = null;
        showMessage(message, "success", "The test payment went through. No money moved.");
        await refresh();
      }
    } finally {
      pay.disabled = false;
    }
  });
  return el("div", { class: "kpi stack" },
    el("div", { class: "kpi-name" }, "Test payment"),
    el("p", {}, "This is a pretend payment page for trying things out. Nothing is charged."),
    el("div", { class: "actions" }, pay),
  );
}

function showPlans(plans, billing) {
  const toggle = select("interval", [["month", "Pay monthly"], ["year", "Pay yearly (saves money)"]], { selected: interval, "aria-label": "How often to pay" });
  toggle.addEventListener("change", () => {
    interval = toggle.value;
    showPlans(plans, billing);
  });
  const chooser = billing.providers.length > 1 ? select("provider", billing.providers.map((p) => [p, PROVIDER_NAME[p] ?? p]), { "aria-label": "How to pay" }) : null;
  plansBox.replaceChildren(
    el("div", { class: "actions" }, toggle, chooser),
    el("div", { class: "kpi-grid" }, ...plans.map((p) => planCard(p, billing, chooser))),
    el("p", { class: "muted" }, "Prices exclude VAT (20%), which is added when you pay."),
  );
}

function planCard(plan, billing, chooser) {
  const price = interval === "year" ? plan.price_year : plan.price_month;
  const sub = billing.subscription;
  const paying = sub.status === "active" || sub.status === "past_due";
  let action = null;
  if (!canManage) action = null;
  else if (!plan.self_serve) action = el("p", { class: "muted" }, plan.vat_note);
  else if (price == null) action = el("p", { class: "muted" }, "Not sold this way");
  else if (plan.current && sub.interval === interval && !sub.scheduled_plan_name) action = el("span", { class: "muted" }, "Your plan");
  else action = chooseButton(plan, paying, chooser);
  return el("div", { class: "kpi stack" },
    el("div", { class: "kpi-name" }, plan.name, " ", plan.current ? el("span", { class: "badge" }, "Current") : null),
    el("div", {}, price == null ? "Talk to us" : `${price} a ${interval}`),
    interval === "year" && plan.year_saving ? el("p", { class: "muted" }, `Saves ${plan.year_saving} a year`) : null,
    el("p", { class: "muted" }, plan.description),
    el("ul", {}, ...plan.features.map((f) => el("li", {}, `${f.label}: ${f.text}`))),
    action,
  );
}

function chooseButton(plan, paying, chooser) {
  const button = el("button", { type: "button" }, paying ? `Move to ${plan.name}` : `Choose ${plan.name}`);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const result = paying
        ? await guard(message, () => api.post(`${base}/change`, { plan_code: plan.code, interval }))
        : await guard(message, () => api.post(`${base}/checkout`, { plan_code: plan.code, interval, provider: chooser?.value || null }));
      if (!result) return;
      const url = result.url ?? result.checkout_url;
      if (url) return goToPayment(url);
      showMessage(message, "success", result.message);
      await refresh();
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

/** Only ever go to a secure page, or back to this site (the test payment page). */
function goToPayment(url) {
  const target = new URL(url, location.href);
  if (target.protocol === "https:" || target.origin === location.origin) location.assign(target.href);
  else showMessage(message, "error", "That payment page could not be opened safely.");
}

function showInvoices(invoices) {
  if (!invoices) {
    invoicesBox.replaceChildren(el("p", { class: "muted" }, "Only the owner can see what has been charged."));
    return;
  }
  invoicesBox.replaceChildren(
    table(
      ["Invoice", "Date", "For", "Net", "VAT", "Total", "Status", ""],
      invoices.map((i) => [
        i.number ?? "-",
        ukDate(i.issued_at),
        i.lines.map((l) => l.description).join("; ") || "-",
        i.net,
        i.vat,
        i.total,
        i.status_label,
        i.hosted_url ? el("a", { href: i.hosted_url, target: "_blank", rel: "noopener noreferrer" }, "View") : "",
      ]),
      { empty: "Nothing has been charged yet." },
    ),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
