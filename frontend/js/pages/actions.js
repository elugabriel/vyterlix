// Actions: the work the business has decided to do about what changed. Everyone in the business can
// look; owners and managers can move the work along, give it to someone, change its dates, tick off
// steps, add notes and evidence. A manager's suggestion outside their own area waits here for the
// owner to approve.

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put, saveBlob, table } from "../data.js";
import { el } from "../dom.js";
import { field, input, orNull, select } from "../forms.js";
import { ukDate, ukDateTime } from "../format.js";
import { loadPeople, statusBadge } from "../actions.js";
import { formatValue, periodLabel } from "../kpi.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const FILTERS = [
  ["open", "Still to do"],
  ["mine", "Mine"],
  ["pending", "Waiting for approval"],
  ["overdue", "Overdue"],
  ["completed", "Done"],
  ["cancelled", "Cancelled"],
  ["all", "Everything"],
];
const MOVE_TEXT = {
  accepted: "Approve and accept",
  in_progress: "Start it",
  partially_completed: "Partly done",
  completed: "Mark as done",
  cancelled: "Cancel it",
};
const KIND_TEXT = {
  created: "Started",
  approved: "Approved",
  rejected: "Turned down",
  status: "Status",
  note: "Note",
  assignment: "Given to",
  dates: "Dates",
  modification: "Changed",
  steps: "Steps",
  evidence: "Evidence",
  outcome: "Result",
  overdue: "Overdue",
};

let orgId = null;
let base = "";
let people = [];
let canWork = false;
let isOwner = false;
let filter = "open";
const list = el("div", { class: "stack" });
const detail = el("div", { class: "stack" });
const counts = el("p", { class: "muted" });
const results = el("div", { class: "stack" });

async function start({ user, org }) {
  orgId = org.id;
  base = `/organizations/${orgId}/actions`;
  isOwner = org.role === "owner";
  canWork = isOwner || org.role === "manager";
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${orgId}`;
  people = await loadPeople(orgId, user);

  const chooser = select("filter", FILTERS, { selected: filter });
  chooser.addEventListener("change", () => {
    filter = chooser.value;
    loadList();
  });
  content.replaceChildren(counts, results, field("Show", chooser), list, detail);
  window.addEventListener("hashchange", openFromHash);
  await Promise.all([loadList(), loadCounts(), loadResults()]);
  await openFromHash();
}

async function loadCounts() {
  const c = await guard(message, () => api.get(`${base}/summary`));
  if (!c) return;
  const parts = [`${c.open} still to do`];
  if (c.overdue) parts.push(`${c.overdue} overdue`);
  if (c.due_soon) parts.push(`${c.due_soon} due within a week`);
  if (c.pending) parts.push(`${c.pending} waiting for approval`);
  parts.push(`${c.completed} done`, `${c.mine_open} of the open ones are yours`);
  counts.textContent = parts.join(" · ");
}

const OUTCOME_CLASS = {
  successful: "health-healthy",
  partially_successful: "health-fair",
  unsuccessful: "health-at_risk",
  inconclusive: "health-not_enough_data",
};

async function loadResults() {
  const r = await guard(message, () => api.get(`${base}/outcomes`));
  if (!r) return;
  const checked = r.successful + r.partially_successful + r.unsuccessful + r.inconclusive;
  results.replaceChildren();
  if (!r.waiting && !checked) return;
  const parts = [];
  if (r.waiting) parts.push(`${r.waiting} finished and waiting to be checked${r.due ? ` (${r.due} ready now)` : ""}`);
  if (checked) parts.push(`${r.successful} worked`, `${r.partially_successful} partly worked`, `${r.unsuccessful} did not work`, `${r.inconclusive} could not be judged`);
  put(
    results,
    el("p", { class: "muted" }, `Results: ${parts.join(" · ")}`),
    r.track_record.length
      ? el(
          "details",
          {},
          el("summary", {}, "How each kind of action has worked for you"),
          table(
            ["Kind of action", "Worked", "Partly", "Did not", "Score"],
            r.track_record.map((t) => [t.name, t.successful, t.partially_successful, t.unsuccessful, `${t.score} out of 100`]),
          ),
          el("p", { class: "muted" }, "The score starts at 50 and moves with each result. It counts for a tenth of how a suggestion is ranked next time."),
        )
      : null,
  );
}

async function loadList() {
  const params = new URLSearchParams();
  if (filter === "open") params.set("open_only", "true");
  else if (filter === "mine") {
    params.set("mine", "true");
    params.set("open_only", "true");
  } else if (filter !== "all") params.set("status", filter);
  const rows = await guard(message, () => api.get(`${base}?${params}`));
  if (!rows) return;
  list.replaceChildren(
    table(
      ["Action", "Area", "Status", "Who", "Finish by", "Steps"],
      rows.map((a) => [
        el("a", { href: `#${a.id}` }, a.title),
        a.kpi_name,
        statusBadge(a),
        a.owner ? a.owner.name : "Nobody yet",
        a.target_date ? [ukDate(a.target_date), a.days_late ? el("span", { class: "status-bad" }, ` (${a.days_late} days late)`) : null] : "No date",
        a.progress.total ? `${a.progress.done} of ${a.progress.total}` : "–",
      ]),
      { empty: "No actions here. Accept a suggestion on the What changed page to start one." },
    ),
  );
}

async function openFromHash() {
  const id = location.hash.slice(1);
  if (!/^[0-9a-f-]{36}$/i.test(id)) return detail.replaceChildren();
  const action = await guard(message, () => api.get(`${base}/${id}`));
  if (action) show(action);
}

async function send(call) {
  const action = await guard(message, call);
  if (action) {
    message.hidden = true;
    show(action);
    loadList();
    loadCounts();
    loadResults();
  }
}

function show(a) {
  const open = a.next_statuses.length > 0;
  const d = a.decision;
  const when = [a.start_date ? `Start ${ukDate(a.start_date)}` : null, a.target_date ? `finish by ${ukDate(a.target_date)}` : null].filter(Boolean).join(", ");
  detail.replaceChildren();
  put(
    detail,
    el("h2", {}, a.title, " ", statusBadge(a)),
    el("p", {}, a.description),
    el("p", { class: "muted" }, `${a.owner ? `With ${a.owner.name}` : "Nobody is doing it yet"}. ${when}.${a.days_late ? ` It is ${a.days_late} days late.` : ""}`),
    el(
      "p",
      { class: "muted" },
      `Meant to improve ${d.kpi_name}: it was ${formatValue(d.baseline_value, d.unit)} in ${periodLabel(d.baseline_period, "month")} when this was accepted. Accepted by ${d.accepted_by ? d.accepted_by.name : "someone"} on ${ukDateTime(d.accepted_at)}${d.modified ? `, changed from "${d.original_title ?? "the suggestion"}"` : ""}.`,
    ),
    resultPanel(a),
    a.status === "pending" && isOwner ? pendingControls(a) : null,
    canWork && open && a.status !== "pending" ? controls(a) : null,
    stepsView(a),
    evidenceView(a),
    timeline(a),
  );
}

function resultPanel(a) {
  if (a.outcome) {
    const o = a.outcome;
    const money = (v) => (v === null ? "–" : formatValue(v, o.unit));
    return el(
      "div",
      { class: "stack" },
      el("h3", {}, "What came of it ", el("span", { class: `badge ${OUTCOME_CLASS[o.outcome]}` }, o.label)),
      el("p", {}, o.reason),
      table(
        ["", "Figure"],
        [
          [`${o.kpi_name} when accepted (${o.baseline_period ? periodLabel(o.baseline_period, "month") : "–"})`, money(o.baseline_value)],
          [`${o.kpi_name} afterwards (${o.measured_period ? periodLabel(o.measured_period, "month") : "no figures"})`, money(o.measured_value)],
          ["Change we expected", money(o.expected_change)],
          ["Change that happened", money(o.actual_change)],
          ["Normal change for that time of year", o.seasonal_change === null ? "Not known (no figures from last year)" : money(o.seasonal_change)],
          ["Change after taking that out", money(o.adjusted_change)],
        ],
      ),
      o.alternative ? el("p", {}, el("strong", {}, "What we now suggest instead: "), `${o.alternative}. See it on the What changed page.`) : null,
      el("p", { class: "muted" }, `Checked on ${ukDateTime(o.measured_at)}${o.measured_by ? ` by ${o.measured_by}` : " automatically"}.`),
      reportButton(a),
    );
  }
  if (!a.follow_up) return null;
  const f = a.follow_up;
  const check = canWork && f.is_due ? el("button", { type: "button", onclick: () => send(() => api.post(`${base}/${a.id}/measure`)) }, "Check the result now") : null;
  return el(
    "div",
    { class: "stack" },
    el("h3", {}, "Checking whether it worked"),
    el("p", {}, f.is_due ? `The check is due. We will compare ${periodLabel(f.measure_month, "month")} with the month you accepted it, as soon as those figures are in.` : `It will be checked from ${ukDate(f.due_date)}, comparing ${periodLabel(f.measure_month, "month")} with the month you accepted it.`),
    check ? el("div", { class: "actions" }, check) : null,
    reportButton(a),
  );
}

function reportButton(a) {
  const out = el("div", { class: "stack" });
  const button = el("button", { type: "button", class: "secondary" }, "Show the full report");
  button.addEventListener("click", async () => {
    const r = await guard(message, () => api.get(`${base}/${a.id}/report`));
    if (!r) return;
    button.hidden = true;
    out.replaceChildren();
    put(
      out,
      el("p", {}, r.summary),
      r.why ? el("p", { class: "muted" }, `Why it was suggested: ${r.why}`) : null,
      r.notes.length ? el("ul", {}, ...r.notes.map((n) => el("li", {}, n))) : null,
      el("p", { class: "muted" }, `${r.steps_done} of ${r.steps_total} steps done · ${r.evidence_count} piece(s) of evidence`),
    );
  });
  return el("div", { class: "stack" }, el("div", { class: "actions" }, button), out);
}

function pendingControls(a) {
  const reason = input("reason", { maxlength: 500, placeholder: "Reason (optional)" });
  const go = (verb) => send(() => api.post(`${base}/${a.id}/${verb}`, { reason: orNull(reason.value) }));
  return el(
    "div",
    { class: "stack" },
    el("p", {}, "Suggested by someone outside this area, so it needs your approval."),
    reason,
    el("div", { class: "actions" }, el("button", { type: "button", onclick: () => go("approve") }, "Approve"), el("button", { type: "button", class: "secondary", onclick: () => go("reject") }, "Turn it down")),
  );
}

function controls(a) {
  const note = input("note", { maxlength: 500, placeholder: "A note about the move (optional)" });
  const moves = a.next_statuses.map((s) =>
    el("button", { type: "button", class: s === "cancelled" ? "secondary" : "", onclick: () => send(() => api.post(`${base}/${a.id}/status`, { status: s, note: orNull(note.value) })) }, MOVE_TEXT[s] ?? s),
  );
  const owner = select("owner", people, { selected: a.owner?.id ?? "", placeholder: "Nobody yet" });
  const start = input("start_date", { type: "date", value: a.start_date ?? "" });
  const target = input("target_date", { type: "date", value: a.target_date ?? "" });
  const save = el("button", { type: "button", class: "secondary" }, "Save who and when");
  save.addEventListener("click", () => send(() => api.patch(`${base}/${a.id}`, { owner_user_id: owner.value || null, start_date: orNull(start.value), target_date: orNull(target.value) })));
  const text = input("text", { maxlength: 2000, placeholder: "Add a note to the history" });
  const addNote = el("button", { type: "button", class: "secondary", onclick: () => text.value.trim() && send(() => api.post(`${base}/${a.id}/notes`, { note: text.value })) }, "Add note");
  return el(
    "div",
    { class: "stack" },
    el("div", { class: "actions" }, ...moves),
    note,
    el("div", { class: "stack" }, field("Who", owner), field("Start", start), field("Finish by", target), el("div", { class: "actions" }, save)),
    el("div", { class: "actions" }, text, addNote),
  );
}

function stepsView(a) {
  if (!a.steps.length) return null;
  const editable = canWork && a.next_statuses.length > 0 && a.status !== "pending";
  const rows = a.steps.map((step, i) => {
    const box = el("input", { type: "checkbox" });
    box.checked = step.done;
    box.disabled = !editable;
    box.addEventListener("change", () => {
      const steps = a.steps.map((s, j) => ({ text: s.text, done: j === i ? box.checked : s.done }));
      send(() => api.patch(`${base}/${a.id}`, { steps }));
    });
    return el("li", {}, el("label", { class: "check" }, box, " ", step.text));
  });
  return el("div", {}, el("strong", {}, `Steps (${a.progress.done} of ${a.progress.total} done)`), el("ul", {}, ...rows));
}

function evidenceView(a) {
  const items = a.evidence.map((e) => {
    const what =
      e.kind === "link"
        ? el("a", { href: e.url, target: "_blank", rel: "noopener noreferrer" }, e.title)
        : e.kind === "file"
          ? el("a", { href: "#", onclick: (ev) => (ev.preventDefault(), download(a, e)) }, `${e.title} (${e.filename})`)
          : el("span", {}, el("strong", {}, e.title), e.note ? ` – ${e.note}` : "");
    return el("li", {}, what, el("span", { class: "muted" }, ` ${e.user ? e.user.name : ""} ${ukDateTime(e.created_at)}`));
  });
  const add = canWork && a.next_statuses.length > 0 && a.status !== "pending" ? evidenceForm(a) : null;
  return el("div", { class: "stack" }, el("strong", {}, "Evidence of the work"), items.length ? el("ul", {}, ...items) : el("p", { class: "muted" }, "Nothing attached yet."), add);
}

async function download(a, e) {
  const blob = await guard(message, () => api.getBlob(`${base}/${a.id}/evidence/${e.id}/file`));
  if (blob) saveBlob(blob, e.filename);
}

function evidenceForm(a) {
  const kind = select("kind", [["note", "A note"], ["link", "A web link"], ["file", "A file"]]);
  const title = input("title", { maxlength: 200, placeholder: "Short title" });
  const text = input("text", { maxlength: 2000, placeholder: "Note, or a link starting https://" });
  const file = el("input", { type: "file", name: "file", accept: ".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.csv,.xlsx,.docx", hidden: true });
  kind.addEventListener("change", () => {
    file.hidden = kind.value !== "file";
    text.hidden = kind.value === "file";
  });
  const add = el("button", { type: "button", class: "secondary" }, "Attach");
  add.addEventListener("click", () => {
    if (kind.value === "file") {
      if (!file.files.length) return;
      const form = new FormData();
      form.append("file", file.files[0]);
      if (title.value.trim()) form.append("title", title.value.trim());
      send(() => api.postForm(`${base}/${a.id}/evidence/file`, form));
    } else {
      const body = { kind: kind.value, title: title.value, [kind.value === "link" ? "url" : "note"]: text.value };
      send(() => api.post(`${base}/${a.id}/evidence`, body));
    }
  });
  return el("div", { class: "actions" }, kind, title, text, file, add);
}

function timeline(a) {
  return el(
    "div",
    { class: "stack" },
    el("strong", {}, "History"),
    el(
      "ul",
      {},
      ...[...a.updates].reverse().map((u) =>
        el("li", {}, el("span", { class: "muted" }, `${ukDateTime(u.created_at)} · ${u.user ? u.user.name : "Vyterlix"} · ${KIND_TEXT[u.kind] ?? u.kind}`), u.note ? el("div", {}, u.note) : null),
      ),
    ),
  );
}

const opened = await openBusiness(message);
if (opened) await start(opened);
