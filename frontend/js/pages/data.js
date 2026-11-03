// Data overview: how complete and trustworthy the business's data is, and what to fix first.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { dataNav, datasetLabel, guard, number, put, scoreBar, severityBadge, table } from "../data.js";
import { el } from "../dom.js";
import { ukDate } from "../format.js";
import { barChart } from "../kpi.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const SOURCE_LABEL = { manual: "Typed in", csv: "CSV file", excel: "Excel file" };


async function start({ org }) {
  const orgId = org.id;
  const canManage = org.role !== "viewer";
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("nav").replaceChildren(dataNav(orgId, "overview"));

  async function load() {
    const report = await guard(message, () => api.get(`/organizations/${orgId}/data-quality`));
    if (report) render(report);
  }

  async function save(button) {
    button.disabled = true;
    const saved = await guard(message, () => api.post(`/organizations/${orgId}/data-quality/refresh`));
    button.disabled = false;
    if (!saved) return;
    render(saved);
    showMessage(
      message,
      "success",
      `Saved. ${saved.new_issues} new ${plural(saved.new_issues, "problem")}, ` +
        `${saved.resolved_issues} fixed since last time.`,
    );
  }

  function render(report) {
    showMessage(message, "info", "");
    content.replaceChildren(
      scoreCard(report, orgId, canManage, save),
      issuesCard(report),
      datasetsCard(report),
      monthsCard(report),
      importsCard(report, orgId),
      originsCard(report),
    );
  }

  await load();
}

const plural = (n, word) => (n === 1 ? word : `${word}s`);

function scoreCard(report, orgId, canManage, save) {
  const card = el("section", { class: "card" });
  if (report.score === null) {
    card.append(
      el("h2", { style: "margin-top:0" }, "Your data quality"),
      el("p", {}, report.headline),
      el(
        "div",
        { class: "actions" },
        el("a", { class: "button", href: `import.html?org=${orgId}` }, "Upload a file"),
        el("a", { class: "button secondary", href: `entry.html?org=${orgId}` }, "Type in a sale"),
      ),
    );
    return card;
  }
  const saveButton = el("button", { type: "button", class: "secondary" }, "Save these problems");
  saveButton.addEventListener("click", () => save(saveButton));
  put(
    card,
    el("h2", { style: "margin-top:0" }, "Your data quality"),
    el(
      "div",
      { class: "score-hero" },
      el("div", {}, el("span", { class: `number band-${report.band}` }, `${report.score}`), el("span", { class: "muted" }, " out of 100")),
      el("p", { style: "flex:1;min-width:240px;margin:0" }, report.headline),
    ),
    canManage
      ? el(
          "p",
          { class: "inline-note" },
          saveButton,
          " Keeps this list of problems so alerts and your health score can use it. The score above is always worked out fresh.",
        )
      : null,
  );
  return card;
}

function issuesCard(report) {
  if (!report.issues.length) {
    return el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, "What to fix first"), el("p", {}, "Nothing to fix right now."));
  }
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "What to fix first"),
    ...report.issues.map((issue) =>
      el(
        "div",
        { class: `issue sev-${issue.severity}` },
        el("p", {}, severityBadge(issue.severity), " ", el("strong", {}, datasetLabel(issue.dataset))),
        el("p", {}, issue.message),
        el("p", { class: "muted" }, issue.fix),
      ),
    ),
  );
}

function datasetsCard(report) {
  const cards = report.datasets.map((d) =>
    el(
      "div",
      { class: "subform" },
      el("h3", {}, `${d.label} `, el("span", { class: "muted" }, `· ${number(d.records)} records`)),
      scoreBar(d.score, `${d.label} score`),
      el(
        "div",
        { class: "component-bars" },
        ...d.components.map((c) => el("div", { class: "component-row", title: c.detail }, el("span", {}, c.label), scoreBar(c.score, c.label))),
      ),
      el(
        "details",
        {},
        el("summary", {}, "What each part means"),
        el("ul", { class: "item-list" }, ...d.components.map((c) => el("li", {}, el("span", {}, c.label, el("br"), el("span", { class: "muted" }, c.detail)), el("span", { class: "score-num" }, `${c.score}`)))),
      ),
    ),
  );
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "How each kind of data is doing"),
    report.missing_datasets.length
      ? el("p", { class: "muted" }, `Nothing recorded yet for: ${report.missing_datasets.join(" and ")}.`)
      : null,
    ...cards,
  );
}

function monthsCard(report) {
  const pct = (v) => (v === null ? "–" : `${v}%`);
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Month by month"),
    barChart(
      [...report.months].sort((x, y) => x.month.localeCompare(y.month)).map((m) => ({ period_start: m.month, is_complete: true, status: m.score === null ? "no_data" : "ok", value: m.score === null ? null : String(m.score), band: m.score >= 80 ? "healthy" : m.score >= 60 ? "fair" : m.score >= 40 ? "needs_attention" : "at_risk" })),
      "count",
      "Data quality score by month",
      { tone: (v) => v.band },
    ),
    el("p", { class: "legend muted" }, ...[["healthy", "80 and over"], ["fair", "60 to 79"], ["needs_attention", "40 to 59"], ["at_risk", "under 40"]].map(([k, t]) => el("span", { class: "legend-item" }, el("i", { class: `swatch swatch-${k}` }), t))),
    el("details", {}, el("summary", {}, "See the list"), table(
      ["Month", "Sales", "Expenses", "Sales with a cost", "Expenses with a category", "Score"],
      report.months.map((m) => [
        m.label,
        number(m.sales_records),
        number(m.expenses_records),
        pct(m.cost_coverage_pct),
        pct(m.categorised_pct),
        m.score === null ? "–" : `${m.score}`,
      ]),
      { empty: "No records yet." },
    )),
  );
}

function importsCard(report, orgId) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "How your recent uploads went"),
    table(
      ["File", "Kind", "Rows imported", "Rows with problems", "Clean"],
      report.imports.map((i) => [
        el("a", { href: `import.html?org=${orgId}&import=${i.import_id}` }, i.filename),
        datasetLabel(i.dataset),
        number(i.rows_imported),
        number(i.rows_with_problems),
        `${i.clean_rate_pct}%`,
      ]),
      { empty: "No uploads yet." },
    ),
    el("p", {}, el("a", { href: `imports.html?org=${orgId}` }, "See the full import history")),
  );
}

function originsCard(report) {
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Where your records came from"),
    table(
      ["Kind of data", "Source", "Records"],
      report.origins.map((o) => [datasetLabel(o.dataset), SOURCE_LABEL[o.source] ?? o.source, el("span", { class: "share-bar" }, el("span", { class: "share-track", "aria-hidden": "true" }, el("span", { class: "share-fill", style: `width:${Math.max(2, (o.records / Math.max(1, ...report.origins.map((x) => x.records))) * 100)}%` })), number(o.records))]),
      { empty: "No records yet." },
    ),
    el("p", { class: "muted" }, `Worked out on ${ukDate(report.as_of)}.`),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
