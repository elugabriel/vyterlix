// Import history: every file uploaded, newest first, with its status.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { DATASETS, STATUS_LABEL, dataNav, datasetLabel, guard, number, requireDataManager, statusBadge, table } from "../data.js";
import { el } from "../dom.js";
import { ukDate } from "../format.js";
import { field, select } from "../forms.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const PAGE = 20;


async function start({ org }) {
  const orgId = org.id;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("nav").replaceChildren(dataNav(orgId, "history"));
  if (!requireDataManager(org, message)) return;

  let offset = 0;
  const status = select("status", Object.entries(STATUS_LABEL), { placeholder: "Any status" });
  const dataset = select("dataset", DATASETS, { placeholder: "Any kind of file" });
  const results = el("div", {});
  const filters = el(
    "div",
    { class: "row" },
    field("Status", status),
    field("Kind of file", dataset),
  );
  for (const control of [status, dataset]) {
    control.addEventListener("change", () => {
      offset = 0;
      load();
    });
  }
  content.replaceChildren(
    el(
      "section",
      { class: "card" },
      el(
        "div",
        { class: "actions", style: "margin:0 0 12px" },
        el("a", { class: "button", href: `import.html?org=${orgId}` }, "Upload a file"),
      ),
      filters,
      results,
    ),
  );

  async function load() {
    const params = new URLSearchParams({ limit: String(PAGE + 1), offset: String(offset) });
    if (status.value) params.set("status", status.value);
    if (dataset.value) params.set("dataset", dataset.value);
    const rows = await guard(message, () => api.get(`/organizations/${orgId}/imports?${params}`));
    if (!rows) return;
    const hasMore = rows.length > PAGE;
    const shown = rows.slice(0, PAGE);
    results.replaceChildren(
      table(
        ["File", "Kind", "Uploaded", "By", "Rows", "Imported", "Status"],
        shown.map((i) => [
          el("a", { href: `import.html?org=${orgId}&import=${i.id}` }, i.original_filename),
          datasetLabel(i.dataset),
          ukDate(i.created_at),
          i.uploaded_by_name ?? "–",
          number(i.row_count),
          i.status === "imported" ? number(i.imported_count) : "–",
          statusBadge(i.status),
        ]),
        { empty: "No uploads match." },
      ),
      el(
        "div",
        { class: "pager" },
        el("button", { type: "button", class: "secondary", disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, "Newer"),
        el("span", { class: "muted" }, shown.length ? `Showing ${offset + 1}–${offset + shown.length}` : ""),
        el("button", { type: "button", class: "secondary", disabled: !hasMore, onclick: () => { offset += PAGE; load(); } }, "Older"),
      ),
    );
  }
  await load();
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
