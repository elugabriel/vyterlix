// Key figures: the business's main numbers (sales, profit, margins...), each with how it moved
// since the period before and how much to trust it. Everyone in the business can look; only the
// owner can ask for the figures to be worked out again.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, jobProgress, number, put, table, waitForJob } from "../data.js";
import { el } from "../dom.js";
import { ukDateTime } from "../format.js";
import { barChart, changeClass, changeText, formatValue, missingText, periodLabel } from "../kpi.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const GRANULARITY = "month";
const CATEGORY_TITLE = { financial: "Money", sales: "Sales" };

let orgId = null;
let base = "";
let isOwner = false;

async function start({ org }) {
  orgId = org.id;
  isOwner = org.role === "owner";
  base = `/organizations/${orgId}/kpis`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;
  await load();
}

async function load() {
  const data = await guard(message, () => api.get(`${base}?granularity=${GRANULARITY}`));
  if (!data) return;
  const header = headerCard(data);
  const groups = {};
  for (const kpi of data.kpis) (groups[kpi.category] ??= []).push(kpi);
  content.replaceChildren(
    header,
    ...Object.entries(groups).map(([category, kpis]) => groupCard(category, kpis)),
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
    el("p", { style: "margin-top:0" }, note),
    !anyValue && !never ? el("p", { class: "muted" }, "There are no sales or expenses to work from yet. Add some under Your data.") : null,
    box,
    progress.node,
    isOwner ? el("div", { class: "actions", style: "margin:8px 0 0" }, button) : el("p", { class: "muted" }, "Only the owner can ask for them to be worked out again."),
  );
}

// --- the figures ---------------------------------------------------------------------------------------------

function groupCard(category, kpis) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, CATEGORY_TITLE[category] ?? category),
    el("div", { class: "kpi-grid" }, ...kpis.map(kpiTile)),
  );
}

function kpiTile(kpi) {
  const latest = kpi.latest;
  const tile = el("div", { class: "kpi" });
  put(tile, el("div", { class: "kpi-name" }, kpi.name));
  if (!latest) {
    put(tile, el("div", { class: "kpi-value muted" }, "–"), el("div", { class: "muted" }, "Not worked out yet"));
    return tile;
  }
  const change = changeText(latest, kpi.unit, GRANULARITY);
  put(
    tile,
    el("div", { class: latest.status === "ok" ? "kpi-value" : "kpi-value muted" }, formatValue(latest.value, kpi.unit)),
    el("div", { class: "muted" }, periodLabel(latest.period_start, GRANULARITY)),
    latest.status !== "ok" ? el("div", { class: "muted" }, missingText(latest)) : null,
    change ? el("div", { class: changeClass(change.sign, kpi.direction) }, `${change.sign > 0 ? "▲" : change.sign < 0 ? "▼" : "•"} ${change.text}`) : null,
    kpi.current && kpi.current.status === "ok"
      ? el("div", { class: "muted" }, `${periodLabel(kpi.current.period_start, GRANULARITY)} so far: ${formatValue(kpi.current.value, kpi.unit)}`)
      : null,
    latest.data_quality !== null && latest.data_quality < 80
      ? el("div", { class: "status-bad" }, `Based on incomplete data (${latest.data_quality}/100)`)
      : null,
  );
  tile.append(detailsFor(kpi));
  return tile;
}

function detailsFor(kpi) {
  const holder = el("div", {});
  const details = el("details", {}, el("summary", {}, "How it's worked out and over time"), holder);
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    const history = await guard(message, () => api.get(`${base}/${kpi.code}?granularity=${GRANULARITY}&limit=24`));
    if (!history) return;
    holder.replaceChildren(
      el("p", { class: "muted" }, kpi.description),
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
