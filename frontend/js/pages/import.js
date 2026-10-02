// Upload a file and bring it into the business, step by step:
//   1 Your file (sheet and heading row)  2 Match columns  3 Check  4 Import (and undo).
// Nothing is added to the business's data until the person presses Import.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import {
  DATASETS,
  dataNav,
  datasetLabel,
  describeCounts,
  guard,
  jobProgress,
  number,
  problemsFilename,
  requireDataManager,
  runJob,
  saveBlob,
  statusBadge,
  put,
  table,
  uuidFromParam,
  waitForJob,
} from "../data.js";
import { el } from "../dom.js";
import { gbp, ukDate } from "../format.js";
import { field, input, orNull, select } from "../forms.js";
import { bindForm, showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const STEPS = ["Your file", "Match columns", "Check", "Import"];
const VAT_RATES = [
  ["20", "20% (standard rate)"],
  ["5", "5% (reduced rate)"],
  ["0", "0% (zero rated or no VAT)"],
];
const DATASET_HINT = {
  sales: "What you sold: one row per sale or order",
  expenses: "What you spent: bills, invoices, receipts",
  customers: "Your list of customers",
  suppliers: "Your list of suppliers",
  products: "Your product list with prices and costs",
  stock_movements: "Deliveries, write-offs and other stock changes",
};

let orgId = null;
let base = "";
let imp = null; // the import being worked on
let duplicate = null; // set when this exact file was uploaded before
let view = "auto"; // "map" shows the column-matching editor
let validation = null;
let result = null; // what the last import or undo did


async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${orgId}/imports`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("nav").replaceChildren(dataNav(orgId, "upload"));
  if (!requireDataManager(org, message)) return;

  const importId = uuidFromParam("import");
  if (!importId) {
    content.replaceChildren(uploadCard());
    return;
  }
  imp = await guard(message, () => api.get(`${base}/${importId}`));
  if (!imp) return;
  await resumeRunningJob();
  await render();
}

/** If this upload has a job queued or running (the page was reloaded), wait for it first. */
async function resumeRunningJob() {
  const jobs = await guard(message, () => api.get(`${base}/${imp.id}/jobs`));
  const active = jobs?.find((j) => j.status === "queued" || j.status === "running");
  if (!active) return;
  const verb = { "import.validate": "Checking", "import.run": "Importing", "import.undo": "Undoing" }[active.kind] ?? "Working";
  const progress = jobProgress(verb);
  content.replaceChildren(el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, `${verb} your file`), progress.node));
  try {
    const done = await waitForJob(orgId, active, progress);
    if (done.kind === "import.run" || done.kind === "import.undo") result = done.result;
    validation = null;
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
  }
  await refreshImport();
}

// --- the page as a whole ------------------------------------------------------------------------

function stepIndex() {
  if (["imported", "undone"].includes(imp.status)) return 3;
  if (view === "map" || ["mapped"].includes(imp.status)) return 1;
  if (imp.status === "validated") return 2;
  return imp.preview?.needs_sheet || view === "auto" ? 0 : 1;
}

function stepper() {
  const current = stepIndex();
  return el(
    "ol",
    { class: "stepper", "aria-label": "Steps" },
    ...STEPS.map((name, i) =>
      el("li", { class: i < current ? "done" : i === current ? "current" : null, "aria-current": i === current ? "step" : null }, name),
    ),
  );
}

async function render() {
  showMessage(message, "info", "");
  const panel = await guard(message, buildPanel);
  if (panel) content.replaceChildren(stepper(), panel);
}

async function buildPanel() {
  if (["imported", "undone"].includes(imp.status)) return donePanel();
  if (imp.file_deleted_at) {
    return el("section", { class: "card" }, el("p", {}, "The original file was deleted after 90 days, so this import can't continue. Please upload it again."), newUploadLink());
  }
  if (imp.status === "failed") {
    return el("section", { class: "card" }, el("p", {}, "This import didn't work. Nothing was added."), newUploadLink());
  }
  if (view === "map") return mappingPanel();
  if (imp.preview?.needs_sheet || imp.status === "uploaded") return filePanel();
  if (imp.status === "mapped") return mappedPanel();
  return checkPanel();
}

const newUploadLink = () => el("a", { class: "button", href: `import.html?org=${orgId}` }, "Upload a file");

async function refreshImport() {
  imp = await api.get(`${base}/${imp.id}`);
}

// --- start: choose a file -----------------------------------------------------------------------

function uploadCard() {
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const kind = select("dataset", DATASETS, { selected: "sales" });
  const hint = el("p", { class: "hint" }, DATASET_HINT.sales);
  kind.addEventListener("change", () => hint.replaceChildren(DATASET_HINT[kind.value] ?? ""));
  const file = el("input", { type: "file", name: "file", accept: ".csv,.xlsx" });
  const form = el(
    "form",
    { novalidate: true },
    field("What is in the file?", kind, null),
    hint,
    field("Your file", file, "A .csv or Excel .xlsx file, up to 25 MB. Nothing is added to your data until you've checked it."),
    el("button", { type: "submit" }, "Upload and read the file"),
  );
  bindForm(form, box, async (v) => {
    const chosen = v.file;
    if (!chosen || !chosen.size) {
      showMessage(box, "error", "Choose a file first.");
      return;
    }
    const data = new FormData();
    data.append("file", chosen);
    data.append("dataset", v.dataset);
    const created = await api.postForm(base, data);
    imp = created;
    duplicate = created.duplicate_of;
    view = "auto";
    history.replaceState(null, "", `import.html?org=${orgId}&import=${created.id}`);
    await render();
  });
  return el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, "Upload a file"), box, form);
}

// --- step 1: the file ---------------------------------------------------------------------------

function fileSize(bytes) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function filePanel() {
  const p = imp.preview;
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const card = el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Your file"),
    el(
      "p",
      {},
      el("strong", {}, imp.original_filename),
      ` · ${datasetLabel(imp.dataset)} · ${fileSize(imp.file_size_bytes)} `,
      statusBadge(imp.status),
    ),
  );
  if (duplicate) {
    card.append(
      el(
        "div",
        { class: "message message-info" },
        `You uploaded this exact file before: “${duplicate.original_filename}” on ${ukDate(duplicate.created_at)} (${duplicate.status}). ` +
          "If you import it again, repeated rows are spotted and skipped.",
      ),
    );
  }
  card.append(box);

  if (p?.needs_sheet) {
    const sheet = select("sheet_name", p.sheets.map((s) => [s, s]));
    const form = el(
      "form",
      { novalidate: true },
      el("p", {}, "This workbook has several sheets. Which one holds your data?"),
      field("Sheet", sheet),
      el("button", { type: "submit" }, "Use this sheet"),
    );
    bindForm(form, box, async (v) => {
      await api.patch(`${base}/${imp.id}`, { sheet_name: v.sheet_name });
      await refreshImport();
      await render();
    });
    card.append(form);
    return card;
  }

  card.append(
    el("p", { class: "muted" }, `We read ${number(imp.row_count)} rows. Here are the first few. Check that the column headings look right.`),
    table(
      p.headers,
      p.sample_rows.slice(0, 6).map((row) => row.map((cell) => cell)),
    ),
  );
  const headerRow = input("header_row", { type: "number", value: imp.header_row, min: 1, max: 100 });
  const form = el(
    "form",
    { novalidate: true },
    el(
      "div",
      { class: "row" },
      field("Which row holds the column headings?", headerRow, "Usually row 1. Change it if your file has a title or notes above the headings."),
    ),
    el("button", { type: "submit", class: "secondary" }, "Use this row"),
  );
  bindForm(form, box, async (v) => {
    await api.patch(`${base}/${imp.id}`, { header_row: Number(v.header_row) });
    await refreshImport();
    await render();
  });
  const next = el("button", { type: "button" }, "Next: match the columns");
  next.addEventListener("click", async () => {
    view = "map";
    await render();
  });
  card.append(form, el("div", { class: "actions" }, next));
  return card;
}

// --- step 2: match the columns ------------------------------------------------------------------

async function mappingPanel() {
  const m = await api.get(`${base}/${imp.id}/mapping`);
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const useSuggestion = !Object.keys(m.mapping).length;
  const chosen = useSuggestion ? m.suggested_mapping : m.mapping;
  const chosenOptions = Object.keys(m.options).length ? m.options : m.suggested_options;
  const groupOf = Object.fromEntries(m.fields.filter((f) => f.one_of).map((f) => [f.key, f.one_of]));

  const rows = m.fields.map((f) => {
    const control = select(`f_${f.key}`, m.headers.map((h) => [h, h]), {
      selected: chosen[f.key] ?? "",
      placeholder: "Not in my file",
      id: `f-${f.key}`,
    });
    return el(
      "div",
      { class: "mapping-row" },
      el("label", { for: `f-${f.key}`, class: f.required ? "required" : null }, f.label),
      control,
      el("p", { class: "help" }, f.help + (groupOf[f.key] ? " (Fill in at least one of these.)" : "")),
    );
  });

  const intro =
    m.suggestion_from === "saved"
      ? `We matched these from your saved layout “${m.saved_source.name}”. Please check them.`
      : m.suggestion_from === "automatic"
        ? "We matched the columns we recognised. Please check them and fill in the rest."
        : "Choose which column in your file holds each piece of information.";

  const parts = [el("p", {}, intro), ...rows];
  if (m.needs_vat_options) parts.push(vatQuestions(m, chosenOptions));
  parts.push(
    el(
      "div",
      { class: "subform" },
      field("Remember this layout as (optional)", input("save_as", { value: m.saved_source?.name ?? "", maxlength: 100, placeholder: "e.g. Till export" }), "Next time you upload a file with the same columns, it matches itself."),
    ),
  );
  const form = el("form", { novalidate: true }, ...parts, el("div", { class: "actions" }, el("button", { type: "submit" }, "Save and continue")));
  const back = el("button", { type: "button", class: "secondary" }, "Back to my file");
  back.addEventListener("click", async () => {
    view = "auto";
    await render();
  });
  form.querySelector(".actions").append(back);

  bindForm(form, box, async (v) => {
    const mapping = {};
    for (const f of m.fields) if (v[`f_${f.key}`]) mapping[f.key] = v[`f_${f.key}`];
    const options = {};
    if (m.needs_vat_options) {
      if (v.vat_inclusive) options.vat_inclusive = v.vat_inclusive === "yes";
      if (v.default_vat_rate) options.default_vat_rate = v.default_vat_rate;
    }
    try {
      await api.put(`${base}/${imp.id}/mapping`, { mapping, options, save_as: orNull(v.save_as) });
    } catch (err) {
      if (err instanceof ApiError && err.code === "mapping_invalid") {
        showMessage(box, "error", "x");
        box.replaceChildren(el("strong", {}, "A few things need sorting out:"), el("ul", {}, ...err.details.map((d) => el("li", {}, d.message))));
        return;
      }
      throw err;
    }
    validation = null;
    view = "auto";
    await refreshImport();
    await render();
  });
  return el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, "Match your columns"), box, form);
}

function vatQuestions(m, options) {
  const radio = (value, label) => {
    const node = el("input", { type: "radio", name: "vat_inclusive", value });
    node.checked = options.vat_inclusive === (value === "yes");
    return el("label", { class: "check" }, node, " ", label);
  };
  return el(
    "div",
    { class: "subform" },
    el("h3", {}, "About the amounts in this file"),
    el("fieldset", {}, el("legend", { class: "required" }, "Do the amounts include VAT?"), radio("yes", "Yes, amounts include VAT"), radio("no", "No, amounts are before VAT")),
    field(
      "VAT rate",
      select("default_vat_rate", VAT_RATES, { selected: options.default_vat_rate ?? "", placeholder: "My file has a VAT column" }),
      "Only needed if your file has no VAT column. UK VAT rates only.",
    ),
  );
}

// --- between steps 2 and 3: matched, not yet checked ---------------------------------------------

async function mappedPanel() {
  const m = await api.get(`${base}/${imp.id}/mapping`);
  const byKey = Object.fromEntries(m.fields.map((f) => [f.key, f.label]));
  const rows = m.fields.filter((f) => m.mapping[f.key]).map((f) => [byKey[f.key], m.mapping[f.key]]);
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const check = el("button", { type: "button" }, "Check my data");
  const change = el("button", { type: "button", class: "secondary" }, "Change the matching");
  change.addEventListener("click", async () => {
    view = "map";
    await render();
  });
  const progress = jobProgress("Checking every row");
  progress.node.hidden = true;
  check.addEventListener("click", async () => {
    check.disabled = change.disabled = true;
    showMessage(box, "info", "");
    progress.node.hidden = false;
    try {
      await runJob(orgId, imp.id, "validate", progress);
      validation = null; // read fresh by the next panel
      await refreshImport();
      await render();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      showMessage(box, "error", err.message);
      progress.node.hidden = true;
      check.disabled = change.disabled = false;
    }
  });
  const vat = m.options.vat_inclusive === undefined ? null : m.options.vat_inclusive ? "Amounts include VAT." : "Amounts are before VAT.";
  return el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Columns matched"),
    table(["Information", "Column in your file"], rows),
    vat ? el("p", { class: "muted" }, vat + (m.options.default_vat_rate ? ` VAT rate ${m.options.default_vat_rate}%.` : "")) : null,
    el("p", { class: "muted" }, "Next we check every row. Nothing is added to your data yet."),
    box,
    progress.node,
    el("div", { class: "actions" }, check, change),
  );
}

// --- step 3: the check --------------------------------------------------------------------------

async function checkPanel() {
  validation ??= await api.get(`${base}/${imp.id}/validation`);
  const v = validation;
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const progress = jobProgress("Importing");
  progress.node.hidden = true;
  const tile = (value, label) => el("div", { class: "tile" }, el("span", { class: "big" }, number(value)), el("span", { class: "label" }, label));
  const card = el(
    "section",
    { class: "card" },
    el("h2", { style: "margin-top:0" }, "Check results"),
    el("div", { class: "tiles" }, tile(v.rows, "rows checked"), tile(v.valid, "ready to import"), tile(v.invalid, "have a problem"), tile(v.duplicate, "repeats (skipped)")),
  );
  if (v.totals) {
    card.append(
      el(
        "p",
        {},
        `The ${number(v.valid)} rows ready to import add up to ${gbp(v.totals.net)} before VAT (${gbp(v.totals.vat)} VAT, ${gbp(v.totals.gross)} in total)` +
          (v.date_from ? `, dated ${ukDate(v.date_from)} to ${ukDate(v.date_to)}.` : "."),
      ),
    );
  }
  for (const w of v.warnings) card.append(el("div", { class: "message message-info" }, w.message));
  if (v.problems.length) {
    card.append(
      el("h3", {}, "What needs fixing"),
      table(
        ["What's wrong", "Rows", "First rows"],
        v.problems.map((p) => [p.example, number(p.count), p.rows.join(", ")]),
      ),
    );
  }
  card.append(box, progress.node);

  const actions = el("div", { class: "actions" });
  const doImport = el("button", { type: "button", disabled: !v.can_import }, `Import ${number(v.valid)} rows`);
  doImport.addEventListener("click", async () => {
    doImport.disabled = true;
    showMessage(box, "info", "");
    progress.node.hidden = false;
    try {
      const job = await runJob(orgId, imp.id, "import", progress);
      result = job.result;
      imp = { ...imp, ...result.data_import };
      await render();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      showMessage(box, "error", err.message);
      progress.node.hidden = true;
      doImport.disabled = false;
    }
  });
  const change = el("button", { type: "button", class: "secondary" }, "Change the matching");
  change.addEventListener("click", async () => {
    view = "map";
    await render();
  });
  actions.append(doImport, change);
  if (v.invalid + v.duplicate > 0) {
    const download = el("button", { type: "button", class: "secondary" }, "Download the rows with problems");
    download.addEventListener("click", async () => {
      await guard(box, async () => saveBlob(await api.getBlob(`${base}/${imp.id}/problems.csv`), problemsFilename(imp.original_filename)));
    });
    actions.append(download);
    card.append(rowBrowser());
  }
  card.append(actions);
  return card;
}

function rowBrowser() {
  const PAGE = 15;
  let offset = 0;
  const kind = select("status", [["invalid", "Rows with problems"], ["duplicate", "Repeated rows"]]);
  const out = el("div", {});
  async function load() {
    const page = await guard(message, () => api.get(`${base}/${imp.id}/rows?status=${kind.value}&limit=${PAGE}&offset=${offset}`));
    if (!page) return;
    out.replaceChildren(
      table(
        ["Row", "What's wrong", "Your values"],
        page.rows.map((r) => [String(r.row_number), r.errors.map((e) => e.message).join(" "), Object.entries(r.raw).map(([k, val]) => `${k}: ${val}`).join(" · ")]),
        { empty: "None." },
      ),
      el(
        "div",
        { class: "pager" },
        el("button", { type: "button", class: "secondary", disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, "Previous"),
        el("span", { class: "muted" }, page.total ? `${offset + 1}–${Math.min(offset + PAGE, page.total)} of ${number(page.total)}` : ""),
        el("button", { type: "button", class: "secondary", disabled: offset + PAGE >= page.total, onclick: () => { offset += PAGE; load(); } }, "Next"),
      ),
    );
  }
  kind.addEventListener("change", () => {
    offset = 0;
    load();
  });
  load();
  return el("div", { class: "subform" }, el("h3", {}, "Look at the rows"), field("Show", kind), out);
}

// --- step 4: done (and undo) -----------------------------------------------------------------------

async function donePanel() {
  const card = el("section", { class: "card" });
  const links = el(
    "div",
    { class: "actions" },
    newUploadLink(),
    el("a", { class: "button secondary", href: `imports.html?org=${orgId}` }, "Import history"),
    el("a", { class: "button secondary", href: `data.html?org=${orgId}` }, "See my data quality"),
  );
  if (imp.status === "undone") {
    put(
    card,
      el("h2", { style: "margin-top:0" }, "This import was undone"),
      el("p", {}, `“${imp.original_filename}” was undone on ${ukDate(imp.undone_at)}.`),
      result?.removed ? el("p", {}, `Taken out of your data: ${describeCounts(result.removed)}.`) : null,
      el("p", { class: "muted" }, "Everything it added has been removed. You can upload the file again after fixing it."),
      links,
    );
    return card;
  }
  const added = result?.created ?? (await api.get(`${base}/${imp.id}/records`)).counts;
  put(
    card,
    el("h2", { style: "margin-top:0" }, "Imported"),
    el("p", {}, `${number(imp.imported_count)} rows from “${imp.original_filename}” are now in your data.`),
    el("p", {}, `Added: ${describeCounts(added)}.`),
    result && (result.skipped_duplicates || result.skipped_invalid)
      ? el("p", { class: "muted" }, `Skipped: ${result.skipped_duplicates} already in your data, ${result.skipped_invalid} that couldn't be read.`)
      : null,
  );
  card.append(undoSection(), links);
  return card;
}

function undoSection() {
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const holder = el("div", { class: "subform" });
  const undoProgress = jobProgress("Undoing");
  const ask = el("button", { type: "button", class: "secondary" }, "Undo this import");
  function showAsk() {
    holder.replaceChildren(el("h3", {}, "Made a mistake?"), el("p", { class: "muted" }, "Undo removes everything this import added. Your other data is not touched."), ask);
  }
  ask.addEventListener("click", () => {
    const yes = el("button", { type: "button", class: "danger" }, "Yes, undo it");
    const no = el("button", { type: "button", class: "secondary" }, "Cancel");
    no.addEventListener("click", showAsk);
    yes.addEventListener("click", async () => {
      yes.disabled = no.disabled = true;
      yes.textContent = "Undoing…";
      try {
        result = (await runJob(orgId, imp.id, "undo", undoProgress)).result;
        imp = { ...imp, ...result.data_import };
        await render();
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        showMessage(box, "error", err.message);
        yes.disabled = no.disabled = false;
        yes.textContent = "Yes, undo it";
      }
    });
    holder.replaceChildren(el("p", {}, "Are you sure? This takes out everything this import added."), el("div", { class: "actions" }, yes, no), undoProgress.node);
  });
  showAsk();
  return el("div", {}, box, holder);
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
