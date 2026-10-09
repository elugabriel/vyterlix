// Reports: your business's figures written up to read, print or send on. A report is written from
// results already worked out and kept exactly as it was written, so it never changes afterwards; you
// can download it as a PDF or a spreadsheet. The owner can also have a report written and emailed
// every week or month to the people they choose (the email links to the report, nothing is attached).

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put, saveBlob, table } from "../data.js";
import { el } from "../dom.js";
import { checkbox, field, input, select } from "../forms.js";
import { ukDateTime } from "../format.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const WEEKDAYS = [[1, "Monday"], [2, "Tuesday"], [3, "Wednesday"], [4, "Thursday"], [5, "Friday"], [6, "Saturday"], [7, "Sunday"]];

let base = "";
let orgId = "";
let isOwner = false;
let members = [];
const cards = el("div", { class: "stack" });
const shown = el("div", { class: "stack" });
const history = el("div", { class: "stack" });

async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${org.id}/reports`;
  isOwner = org.role === "owner";
  document.getElementById("org-name").textContent = org.name;
  if (isOwner) members = (await guard(message, () => api.get(`/organizations/${org.id}/members`))) ?? [];
  content.replaceChildren(
    el("p", { class: "muted" }, "Choose a report and write it now. It is kept exactly as it was written, so you can always come back to it, and it is only ever shown to you."),
    cards,
    shown,
    el("h2", {}, "Reports written for you"),
    history,
  );
  await Promise.all([loadCards(), loadHistory()]);
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
}

async function loadCards() {
  const rows = await guard(message, () => api.get(base));
  if (!rows) return;
  cards.replaceChildren(...rows.map(card));
}

function card(r) {
  const write = el("button", { type: "button" }, "Write it now");
  write.addEventListener("click", async () => {
    write.disabled = true;
    try {
      const run = await guard(message, () => api.post(`${base}/${r.id}/generate`));
      if (run) {
        message.hidden = true;
        location.hash = run.id;
        showRun(run);
        loadHistory();
        loadCards();
      }
    } finally {
      write.disabled = false;
    }
  });
  const latest = r.last_run ? el("a", { href: `#${r.last_run.id}` }, `Open the latest (${ukDateTime(r.last_run.generated_at)})`) : el("span", { class: "muted" }, "Nothing written for you yet");
  const months = isOwner && !r.fixed_months ? monthsControl(r) : el("span", { class: "muted" }, r.fixed_months ? "Covers the latest month" : `Covers the last ${r.months} months`);
  return el(
    "div",
    { class: "kpi stack" },
    el("div", { class: "kpi-name" }, r.name),
    el("p", {}, r.description),
    el("div", { class: "actions" }, write, latest, months),
    isOwner ? schedules(r) : null,
  );
}

function monthsControl(r) {
  const box = input("months", { type: "number", value: r.months, min: 1, max: 24, "aria-label": "Months to cover" });
  box.style.maxWidth = "5rem";
  const save = el("button", { type: "button", class: "secondary" }, "Save");
  save.addEventListener("click", async () => {
    const saved = await guard(message, () => api.put(`${base}/${r.id}/months`, { months: Number(box.value) }));
    if (saved) {
      message.hidden = true;
      save.textContent = "Saved";
      setTimeout(() => (save.textContent = "Save"), 1200);
    }
  });
  return el("span", { class: "actions", style: "margin-top:0" }, el("span", { class: "muted" }, "Covers the last"), box, el("span", { class: "muted" }, "months"), save);
}

// --- scheduled delivery (the owner) -------------------------------------------------------------------

function schedules(r) {
  const list = r.schedules.map((s) =>
    el(
      "li",
      {},
      `${s.when} to ${s.recipients.join(", ") || "nobody"}`,
      el("span", { class: "muted" }, ` · next ${ukDateTime(s.next_run_at)}${s.enabled ? "" : " · paused"}`),
      " ",
      el("button", { type: "button", class: "secondary", onclick: () => toggle(s) }, s.enabled ? "Pause" : "Resume"),
      " ",
      el("button", { type: "button", class: "secondary", onclick: () => remove(s) }, "Stop"),
    ),
  );
  return el("details", {}, el("summary", {}, r.schedules.length ? `Sent to people automatically (${r.schedules.length})` : "Send it to people automatically"), el("div", { class: "stack" }, list.length ? el("ul", {}, ...list) : null, scheduleForm(r)));
}

function scheduleForm(r) {
  const frequency = select("frequency", [["weekly", "Every week"], ["monthly", "Every month"]]);
  const weekday = select("weekday", WEEKDAYS, { selected: 1 });
  const day = input("day_of_month", { type: "number", value: 1, min: 1, max: 28 });
  const dayField = field("Day of the month (1 to 28)", day);
  const weekdayField = field("Day of the week", weekday);
  const sync = () => {
    weekdayField.hidden = frequency.value !== "weekly";
    dayField.hidden = frequency.value !== "monthly";
  };
  frequency.addEventListener("change", sync);
  sync();
  const people = members.filter((m) => m.status === "active").map((m) => checkbox(`to-${m.user_id}`, `${m.full_name} (${m.email})`, { checked: false, "data-user": m.user_id }));
  const add = el("button", { type: "button" }, "Start sending");
  add.addEventListener("click", async () => {
    const recipients = [...document.querySelectorAll(`#r-${r.id} input[data-user]`)].filter((b) => b.checked).map((b) => b.dataset.user);
    const body = { frequency: frequency.value, weekday: frequency.value === "weekly" ? Number(weekday.value) : null, day_of_month: frequency.value === "monthly" ? Number(day.value) : null, recipients };
    const made = await guard(message, () => api.post(`${base}/${r.id}/schedules`, body));
    if (made) {
      message.hidden = true;
      loadCards();
    }
  });
  return el("div", { class: "stack", id: `r-${r.id}` }, el("strong", {}, "A new schedule"), field("How often", frequency), weekdayField, dayField, el("strong", {}, "Who should get it (a link to the report, by email)"), ...people, el("div", { class: "actions" }, add));
}

async function toggle(s) {
  await guard(message, () => api.patch(`${base}/schedules/${s.id}`, { enabled: !s.enabled }));
  loadCards();
}

async function remove(s) {
  if (!confirm("Stop sending this report?")) return;
  await guard(message, () => api.delete(`${base}/schedules/${s.id}`));
  loadCards();
}

// --- reading a report --------------------------------------------------------------------------------------

function section(s) {
  return el(
    "div",
    { class: "stack" },
    el("h3", {}, s.heading),
    ...s.paragraphs.map((p) => el("p", {}, p)),
    s.bullets.length ? el("ul", {}, ...s.bullets.map((b) => el("li", {}, b))) : null,
    s.table ? table(s.table.columns, s.table.rows, { empty: "Nothing here." }) : null,
  );
}

async function download(run, format) {
  const blob = await guard(message, () => api.getBlob(`${base}/runs/${run.id}/${format}`));
  if (blob) saveBlob(blob, `vyterlix-${run.kind}-${run.period_end.slice(0, 7)}.${format === "pdf" ? "pdf" : "csv"}`);
}

function showRun(run) {
  const c = run.content;
  shown.replaceChildren();
  put(
    shown,
    el("h2", {}, c.title),
    ...c.facts.map((f) => el("p", { class: "muted" }, f)),
    el("div", { class: "actions" }, el("button", { type: "button", onclick: () => download(run, "pdf") }, "Download as a PDF"), el("button", { type: "button", class: "secondary", onclick: () => download(run, "csv") }, "Download for a spreadsheet")),
    ...c.sections.map(section),
    ...c.notes.map((n) => el("p", { class: "muted" }, n)),
  );
  shown.scrollIntoView({ block: "start" });
}

async function openFromHash() {
  const id = location.hash.slice(1);
  if (!/^[0-9a-f-]{36}$/i.test(id)) return;
  const run = await guard(message, () => api.get(`${base}/runs/${id}`));
  if (run) showRun(run);
}

async function loadHistory() {
  const rows = await guard(message, () => api.get(`${base}/runs`));
  if (!rows) return;
  history.replaceChildren(
    table(
      ["Report", "Covers", "Written", "How", ""],
      rows.map((r) => [r.title, `${r.period_start.slice(0, 7)} to ${r.period_end.slice(0, 7)}`, ukDateTime(r.generated_at), r.trigger === "scheduled" ? (r.emailed ? "Sent to you" : "Scheduled") : "By you", el("a", { href: `#${r.id}` }, "Open")]),
      { empty: "None yet. Write one above." },
    ),
  );
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
