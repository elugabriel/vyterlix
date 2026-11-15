// Key figures: the business's main numbers (sales, profit, margins...), each with how it moved
// since the period before and how much to trust it. Everyone in the business can look; only the
// owner can ask for the figures to be worked out again.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, jobProgress, number, put, table, waitForJob } from "../data.js";
import { el } from "../dom.js";
import { gbp, ukDate, ukDateTime } from "../format.js";
import { barChart, changeClass, changeText, formatValue, missingText, periodLabel, plainBlock, shareBar, sparkline } from "../kpi.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const GRANULARITY = "month";
const CATEGORY_TITLE = { financial: "Financial Health", sales: "Sales", customer: "Customers", inventory: "Stock", marketing: "Your website" };
const CATEGORY_ORDER = ["financial", "sales", "customer", "inventory", "marketing"];

let orgId = null;
let base = "";
let isOwner = false;
let sells = null; // products, services, both, or null if they have not said

async function start({ org }) {
  orgId = org.id;
  isOwner = org.role === "owner";
  sells = org.sells ?? null;
  base = `/organizations/${orgId}/kpis`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;
  await load();
}

async function load() {
  const data = await guard(message, () => api.get(`${base}?granularity=${GRANULARITY}`));
  if (!data) return;
  const header = headerCard(data);
  // The figures are grouped into the parts of Vyterlix this business sees (Sales & Revenue, Bookings &
  // Appointments...). Older answers without modules fall back to the five plain groups.
  const parts = data.modules?.length
    ? data.modules.map((m) => ({ code: m.code, name: m.name, summary: m.summary }))
    : CATEGORY_ORDER.map((c) => ({ code: c, name: CATEGORY_TITLE[c], summary: null }));
  const groups = {};
  for (const kpi of data.kpis) (groups[data.modules?.length ? kpi.module : kpi.category] ??= []).push(kpi);
  const slots = el("div", { class: "stack" });
  content.replaceChildren(header, ...parts.filter((p) => groups[p.code]).map((p) => groupCard(p, groups[p.code])), slots);
  drawSparks();
  await loadBreakdowns(slots);
}

// --- where sales come from, and which stock to watch (read straight from the records) ---------------------

async function loadBreakdowns(slots) {
  const noStock = sells === "services";
  const [channels, products, movers, followUp, serviceList, teamList, projectList] = await Promise.all([
    guard(message, () => api.get(`${base}/breakdown/channel`)),
    guard(message, () => api.get(`${base}/breakdown/product`)),
    noStock ? null : guard(message, () => api.get(`${base}/stock/movers`)),
    sells === "products" ? null : guard(message, () => api.get(`${base}/customers/follow-up`)),
    sells === "products" ? null : guard(message, () => api.get(`${base}/services`)),
    sells === "products" ? null : guard(message, () => api.get(`${base}/team`)),
    guard(message, () => api.get(`${base}/projects`)),
  ]);
  if (channels && channels.rows.length) slots.append(breakdownCard("Where your sales come from", "Sales", channels, false));
  if (products && products.rows.length) slots.append(breakdownCard("Your best sellers", "Items sold", products, true));
  if (projectList && projectList.rows.length) slots.append(projectsCard(projectList));
  if (serviceList && serviceList.rows.length) slots.append(servicesCard(serviceList));
  if (teamList && teamList.members.length) slots.append(teamCard(teamList));
  if (followUp && followUp.customers.length) slots.append(followUpCard(followUp));
  if (movers && (movers.fast.length || movers.slow.length || movers.dead.length)) slots.append(moversCard(movers));
}

function breakdownCard(title, countHeading, data, withProfit) {
  const headers = ["", "Sales", "Share", countHeading, ...(withProfit ? ["Profit"] : [])];
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, title),
    el("p", { class: "muted" }, `${ukDate(data.period_from)} to ${ukDate(data.period_to)}, before VAT, after refunds.`),
    table(
      headers,
      data.rows.map((r) => [
        r.label,
        gbp(r.revenue),
        r.share_pct === null ? "–" : shareBar(Number(r.share_pct)),
        number(r.count),
        ...(withProfit ? [r.gross_profit === null ? "–" : gbp(r.gross_profit)] : []),
      ]),
      { empty: "No sales in this period." },
    ),
  );
}

function projectsCard(data) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Your projects"),
    ...data.notes.map((n) => el("p", {}, n)),
    table(
      ["Project", "Earned", "Cost", "Profit", "Margin", "Budget used"],
      data.rows.map((r) => [
        r.client ? `${r.name} (${r.client})` : r.name,
        gbp(r.revenue),
        gbp(r.costs),
        el("span", { class: Number(r.profit) < 0 ? "status-bad" : "" }, gbp(r.profit)),
        r.margin_pct === null ? "–" : `${r.margin_pct}%`,
        r.budget_used_pct === null ? "No budget" : shareBar(Number(r.budget_used_pct), { over: 100 }),
      ]),
    ),
    el("p", { class: "muted" }, "Sales and costs before VAT, for the sales and costs you put on a project."),
  );
}

function teamCard(data) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Your team"),
    ...data.notes.map((n) => el("p", {}, n)),
    table(
      ["Who", "Kept", "Cancelled", "Did not turn up", "Share of the work", "Average rating"],
      data.members.map((m) => [m.staff, number(m.kept), number(m.cancelled), number(m.missed), m.share_pct === null ? "–" : shareBar(Number(m.share_pct)), m.average_rating === null ? "Not rated" : `${m.average_rating} out of 5`]),
    ),
    el("p", { class: "muted" }, `The last three months of appointments, up to ${ukDate(data.period_to)}.`),
  );
}

function servicesCard(data) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Your services"),
    ...data.notes.map((n) => el("p", {}, n)),
    table(
      ["Service", "Kept", "Cancelled", "Did not turn up", "Booked again"],
      data.rows.map((r) => [r.service, number(r.kept), number(r.cancelled), number(r.missed), r.rebooked_pct === null ? "Not known" : `${r.rebooked_pct}%`]),
    ),
    el("p", { class: "muted" }, `The last three months of appointments, up to ${ukDate(data.period_to)}. "Booked again" is the share of customers who have another appointment after their last one.`),
  );
}

function followUpCard(data) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Customers to follow up"),
    el("p", { class: "muted" }, `${data.total} ${data.total === 1 ? "customer has" : "customers have"} not bought for ${data.quiet_days} days or more. These spent the most with you, so they are worth a friendly message.`),
    table(
      ["Customer", "Last bought", "Quiet for", "Orders", "Spent"],
      data.customers.map((c) => [c.name, ukDate(c.last_bought_on), `${number(c.days_since)} days`, number(c.orders), gbp(c.total_spent)]),
    ),
  );
}

function moversCard(movers) {
  const section = (heading, note, rows) =>
    rows.length
      ? el(
          "div",
          {},
          el("h3", {}, heading),
          el("p", { class: "muted" }, note),
          table(
            ["Product", "Sold", "In stock", "Stock value", "Last sold"],
            rows.map((m) => [
              m.name,
              number(m.units_sold),
              number(m.on_hand),
              m.stock_value === null ? "–" : gbp(m.stock_value),
              m.last_sold_on ? ukDate(m.last_sold_on) : "Never",
            ]),
          ),
        )
      : null;
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Stock to watch"),
    el("p", { class: "muted" }, `Looking at the last ${movers.days} days.`),
    section("Selling fastest", "Make sure you don't run out.", movers.fast),
    section("Selling slowest", "Selling, but not much.", movers.slow),
    section("Not selling at all", "In stock, but nothing sold in this time: money sitting on the shelf.", movers.dead),
  );
}

// --- the top: when it was worked out, and the recalculate button -----------------------------------------

function headerCard(data) {
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const progress = jobProgress("Working out your figures");
  progress.node.hidden = true;
  const run = data.last_run;
  const never = !run || run.status !== "succeeded";
  const anyValue = data.kpis.some((k) => k.latest || k.current);
  const button = el("button", { type: "button", class: never || !anyValue ? null : "secondary" }, never ? "Work out my figures" : "Work them out again");
  button.addEventListener("click", async () => {
    button.disabled = true;
    showMessage(box, "info", "");
    progress.node.hidden = false;
    try {
      const job = await api.post(`${base}/calculate?granularity=${GRANULARITY}`);
      await waitForJob(orgId, job, progress);
      await load();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      showMessage(box, "error", err.message);
      progress.node.hidden = true;
      button.disabled = false;
    }
  });
  const note = never
    ? "Your figures haven't been worked out yet."
    : `Worked out on ${ukDateTime(run.finished_at ?? run.started_at)}. They update by themselves after an import.`;
  return el(
    "section",
    { class: "card" },
    el("p", { style: "margin-top:0", class: "muted" }, note),
    !anyValue && !never ? el("p", { class: "muted" }, "There are no sales or expenses to work from yet. Add some under Your data.") : null,
    box,
    progress.node,
    isOwner ? el("div", { class: "actions", style: "margin:8px 0 0" }, button) : el("p", { class: "muted" }, "Only the owner can ask for them to be worked out again."),
  );
}

// --- the figures ---------------------------------------------------------------------------------------------

function groupCard(part, kpis) {
  return el(
    "section",
    { class: "card", id: `module-${part.code}` },
    el("h2", { style: "margin-top:0" }, part.name ?? part.code),
    part.summary ? el("p", { class: "muted", style: "margin-top:-8px" }, part.summary) : null,
    el("div", { class: "kpi-grid" }, ...kpis.map(kpiTile)),
  );
}

function kpiTile(kpi) {
  const latest = kpi.latest;
  const tile = el("div", { class: "kpi", id: kpi.code }); // so other pages can link straight to it
  put(tile, el("div", { class: "kpi-name" }, kpi.name));
  if (!latest) {
    put(tile, el("div", { class: "kpi-value muted" }, "–"), el("div", { class: "muted" }, "Not worked out yet"));
    return tile;
  }
  const change = changeText(latest, kpi.unit, GRANULARITY);
  const spark = el("div", { class: "spark-slot" });
  put(
    tile,
    el("div", { class: latest.status === "ok" ? "kpi-value" : "kpi-value muted" }, formatValue(latest.value, kpi.unit)),
    latest.status !== "ok" ? el("div", { class: "muted" }, missingText(latest, kpi.requires)) : null,
    change ? el("div", { class: `${changeClass(change.sign, kpi.direction)} kpi-change` }, `${change.sign > 0 ? "▲" : change.sign < 0 ? "▼" : "•"} ${change.text.replace("the month before", "last month")}`) : null,
    spark,
    latest.data_quality !== null && latest.data_quality < 80
      ? el("div", { class: "status-bad kpi-change" }, `Some data missing (${latest.data_quality}/100)`)
      : null,
  );
  tile.append(detailsFor(kpi, latest));
  if (latest.status === "ok") sparks.push([kpi, spark]);
  return tile;
}

// The little six-month picture in each tile is fetched after the page is up, so the numbers show straight away.
const sparks = [];
async function drawSparks() {
  await Promise.all(
    sparks.splice(0).map(async ([kpi, slot]) => {
      const history = await api.get(`${base}/${kpi.code}?granularity=${GRANULARITY}&limit=6`).catch(() => null);
      const picture = history ? sparkline(history.values, `${kpi.name}, last months`) : null;
      if (picture) slot.append(picture, el("div", { class: "spark-caption muted" }, `${periodLabel(history.values.filter((v) => v.status === "ok")[0].period_start, GRANULARITY).slice(0, 3)} to ${periodLabel(kpi.latest.period_start, GRANULARITY).slice(0, 3)}`));
    }),
  );
}

function detailsFor(kpi, latest) {
  const holder = el("div", {});
  const details = el("details", {}, el("summary", {}, "More about this figure"), holder);
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    const history = await guard(message, () => api.get(`${base}/${kpi.code}?granularity=${GRANULARITY}&limit=24`));
    if (!history) return;
    holder.replaceChildren(
      plainBlock(kpi.plain),
      el("p", { class: "muted" }, kpi.description),
      kpi.current && kpi.current.status === "ok" ? el("p", { class: "muted" }, `${periodLabel(kpi.current.period_start, GRANULARITY)} so far: ${formatValue(kpi.current.value, kpi.unit)}`) : null,
      barChart(history.values, kpi.unit, `${kpi.name} by month`),
      table(
        ["Month", "Figure", "Data quality"],
        [...history.values].reverse().map((v) => [
          periodLabel(v.period_start, GRANULARITY) + (v.is_complete ? "" : " (so far)"),
          v.status === "ok" ? formatValue(v.value, kpi.unit) : "–",
          v.data_quality === null ? "–" : `${number(v.data_quality)}/100`,
        ]),
        { empty: "No figures yet." },
      ),
    );
  });
  return details;
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
