// Shared by the data pages (overview, upload, history, typing in): labels, the sub-navigation,
// a table builder and a few display helpers. Everything is built with el(), never innerHTML.

import { ApiError } from "./api.js";
import { api } from "./auth.js";
import { el } from "./dom.js";
import { showMessage } from "./ui.js";

export const DATASETS = [
  ["sales", "Sales"],
  ["expenses", "Expenses"],
  ["customers", "Customers"],
  ["suppliers", "Suppliers"],
  ["products", "Products"],
  ["stock_movements", "Stock movements"],
];

const DATASET_LABEL = Object.fromEntries(DATASETS);

export const datasetLabel = (code) => DATASET_LABEL[code] ?? code;

export const STATUS_LABEL = {
  uploaded: "Uploaded",
  mapped: "Columns matched",
  validated: "Checked",
  importing: "Importing",
  imported: "Imported",
  failed: "Failed",
  undone: "Undone",
};

// What was created, in words a business owner uses.
const RECORD_LABEL = {
  sales: "sales",
  sale_lines: "sale lines",
  expenses: "expenses",
  customers: "customers",
  suppliers: "suppliers",
  products: "products",
  stock_movements: "stock movements",
  business_list_items: "list entries (channels, categories...)",
};

/** {"sales": 3, "customers": 2} -> "3 sales, 2 customers" */
export function describeCounts(counts) {
  const parts = Object.entries(counts ?? {})
    .filter(([, n]) => n > 0)
    .map(([table, n]) => `${n.toLocaleString("en-GB")} ${RECORD_LABEL[table] ?? table}`);
  return parts.length ? parts.join(", ") : "nothing";
}

export function statusBadge(status) {
  return el("span", { class: `badge status-${status}` }, STATUS_LABEL[status] ?? status);
}

export function severityBadge(severity) {
  const text = { critical: "Fix first", warning: "Should fix", info: "Worth knowing" }[severity] ?? severity;
  return el("span", { class: `badge sev-${severity}` }, text);
}

export const number = (n) => Number(n).toLocaleString("en-GB");

/** The sub-navigation shown on every data page. `active` is the current page's key. */
export function dataNav(orgId, active) {
  const link = (key, href, text) =>
    el("a", { href: `${href}?org=${orgId}`, class: key === active ? "current" : null, "aria-current": key === active ? "page" : null }, text);
  return el(
    "nav",
    { class: "subnav", "aria-label": "Your data" },
    el("a", { href: `business.html?org=${orgId}` }, "← Business"),
    link("overview", "data.html", "Data overview"),
    link("upload", "import.html", "Upload a file"),
    link("history", "imports.html", "Import history"),
    link("entry", "entry.html", "Type in data"),
  );
}

/** Viewers can read the quality score but not manage data. Shows why and returns false. */
export function requireDataManager(org, message) {
  if (org.role === "viewer") {
    showMessage(message, "info", "Only owners and managers can add or change data. You can still see the data overview.");
    return false;
  }
  return true;
}

/** parent.append(...) that skips empty values (append(null) would print the word "null"). */
export function put(parent, ...children) {
  parent.append(...children.filter((child) => child != null && child !== false));
  return parent;
}

/** Run an action; show an API error in `box` instead of letting it escape. */
export async function guard(box, action) {
  try {
    return await action();
  } catch (err) {
    if (!(err instanceof ApiError)) {
      showMessage(box, "error", "Something went wrong. Please try again.");
      throw err;
    }
    showMessage(box, "error", err.message);
    return undefined;
  }
}

/** A simple table: headers = ["Date", ...]; rows = arrays of strings or nodes. */
export function table(headers, rows, { empty = "Nothing here yet.", className = "list" } = {}) {
  if (!rows.length) return el("p", { class: "muted" }, empty);
  return el(
    "div",
    { class: "scroll" },
    el(
      "table",
      { class: className },
      el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, ...rows.map((row) => el("tr", {}, ...row.map((cell) => el("td", {}, cell))))),
    ),
  );
}

/** Save a Blob as a file download (the API's CSV of problem rows). */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** "till.csv" -> "problems - till.csv" (safe to use as a download name). */
export function problemsFilename(original) {
  const stem = String(original).replace(/\.[^.]+$/, "").replace(/[^\w .-]+/g, "_");
  return `problems - ${stem}.csv`;
}

export function uuidFromParam(name) {
  const id = new URLSearchParams(location.search).get(name) ?? "";
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id : null;
}

// --- background jobs -------------------------------------------------------------------------------
// Checking, importing and undoing run in the background worker; the page starts a job and polls it.

const QUEUE_PATIENCE_MS = 15_000; // queued this long: the worker is probably not running
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A progress bar plus a sentence. `update(job, waitedMs)` redraws it. */
export function jobProgress(verb) {
  const bar = el("progress", { max: 100, "aria-label": `${verb} progress` });
  const text = el("p", { class: "muted", role: "status" }, "Starting…");
  function update(job, waited) {
    if (job.status === "queued") {
      bar.removeAttribute("value");
      text.textContent =
        waited > QUEUE_PATIENCE_MS
          ? "Still waiting for the background worker. If this carries on, the worker may not be running."
          : "Waiting for the background worker…";
      return;
    }
    if (job.percent === null || job.percent === undefined) {
      bar.removeAttribute("value");
      text.textContent = `${verb}…`;
      return;
    }
    bar.value = job.percent;
    const rows = job.progress_total ? ` (${number(job.progress_done)} of ${number(job.progress_total)} rows)` : "";
    text.textContent = `${verb}… ${job.percent}%${rows}`;
  }
  return { node: el("div", { class: "job-progress" }, el("div", { class: "progress-row" }, bar), text), update };
}

/** Poll a job until it finishes. Returns the finished job; throws ApiError if it failed. */
export async function waitForJob(orgId, job, progress) {
  const started = Date.now();
  while (job.status === "queued" || job.status === "running") {
    progress?.update(job, Date.now() - started);
    await wait(Date.now() - started > 30_000 ? 2000 : 1000);
    job = await api.get(`/organizations/${orgId}/jobs/${job.id}`);
  }
  if (job.status === "failed") {
    throw new ApiError(200, job.error_code ?? "job_failed", job.error_message ?? "This did not finish. Please try again.");
  }
  return job;
}

/** Start a background job on an upload ("validate", "import" or "undo") and wait for it. */
export async function runJob(orgId, importId, action, progress) {
  const job = await api.post(`/organizations/${orgId}/imports/${importId}/jobs`, { action });
  return waitForJob(orgId, job, progress);
}

/** A bar for a 0-100 score, with its number as text so it's readable without colour. */
export function scoreBar(score, label) {
  return el(
    "div",
    { class: "score-bar" },
    el("progress", { max: 100, value: score, "aria-label": label }),
    el("span", { class: "score-num" }, `${score}`),
  );
}
