// Ask Vyterlix: questions about the business answered only from its own results. Every answer says
// where it came from and which engine wrote it; a question that is not understood gets an honest "I
// do not know". The owner decides whether the assistant is on and whether outside AI may reword
// answers (off until they turn it on).

import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { guard, put } from "../data.js";
import { el } from "../dom.js";
import { checkbox } from "../forms.js";
import { ukDateTime } from "../format.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const EXAMPLES = ["How are we doing?", "What were my sales last month?", "Why did sales fall?", "What should I do about it?", "Forecast my sales", "What are my actions?", "Did it work?"];

let base = "";
let isOwner = false;
let conversationId = null;
const thread = el("div", { class: "stack" });
const past = el("div", { class: "stack" });
const box = el("input", { type: "text", name: "question", maxlength: 600, placeholder: "Ask about your business...", "aria-label": "Your question" });
const send = el("button", { type: "submit" }, "Ask");
const settingsBox = el("div", { class: "stack" });

async function start({ org }) {
  base = `/organizations/${org.id}/assistant`;
  isOwner = org.role === "owner";
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("back").href = `business.html?org=${org.id}`;
  const form = el("form", { class: "actions" }, box, send);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(box.value);
  });
  const chips = el("div", { class: "actions" }, ...EXAMPLES.map((q) => el("button", { type: "button", class: "secondary", onclick: () => ask(q) }, q)));
  const fresh = el("button", { type: "button", class: "secondary", onclick: newConversation }, "New conversation");
  content.replaceChildren(
    el("p", { class: "muted" }, "Ask a question about your business. Answers come only from your own figures and records, say where they came from, and never guess. If something cannot be answered from your records, you will be told so."),
    thread,
    form,
    chips,
    el("div", { class: "actions" }, fresh),
    el("h2", {}, "Your earlier conversations"),
    past,
    el("h2", {}, "About AI and your data"),
    settingsBox,
  );
  await Promise.all([loadPast(), loadSettings()]);
}

function newConversation() {
  conversationId = null;
  thread.replaceChildren();
  box.focus();
}

function bubble(m) {
  const mine = m.role === "user";
  const parts = [el("div", {}, el("strong", {}, mine ? "You" : "Vyterlix"), " ", el("span", { class: "muted" }, ukDateTime(m.created_at)))];
  for (const line of m.content.split("\n")) parts.push(el("p", {}, line));
  if (!mine) {
    if (m.sources.length) {
      parts.push(el("p", { class: "muted" }, "From: ", ...m.sources.flatMap((s, i) => [i ? ", " : "", s.link ? el("a", { href: `${s.link.split("#")[0]}${location.search}${s.link.includes("#") ? `#${s.link.split("#")[1]}` : ""}` }, s.label) : s.label])));
    }
    parts.push(el("p", { class: "muted" }, `Answered by ${m.engine === "vyterlix-grounded" ? "Vyterlix, from your own results" : m.engine}${m.sent_outside ? ". The facts behind this answer were sent to an outside AI provider, as you allowed." : ". Nothing left Vyterlix."}`));
    if (m.tool_calls.length) {
      parts.push(el("details", {}, el("summary", {}, "What was looked up"), el("ul", {}, ...m.tool_calls.map((c) => el("li", {}, `${c.tool}: ${c.ok ? `${c.facts.length} fact(s) found` : "nothing found"}`)))));
    }
  }
  return el("div", { class: `kpi ${mine ? "" : "answer"}` }, ...parts);
}

async function ask(text) {
  const question = text.trim();
  if (!question) return;
  send.disabled = true;
  box.value = "";
  try {
    const result = await guard(message, () => api.post(`${base}/ask`, { message: question, conversation_id: conversationId }));
    if (!result) return;
    message.hidden = true;
    conversationId = result.conversation_id;
    thread.append(bubble(result.question), bubble(result.answer));
    thread.lastElementChild.scrollIntoView({ block: "nearest" });
    loadPast();
  } finally {
    send.disabled = false;
  }
}

async function loadPast() {
  const rows = await guard(message, () => api.get(`${base}/conversations`));
  if (!rows) return;
  past.replaceChildren();
  if (!rows.length) return put(past, el("p", { class: "muted" }, "None yet."));
  put(
    past,
    ...rows.map((c) =>
      el(
        "div",
        { class: "actions" },
        el("a", { href: "#", onclick: (e) => (e.preventDefault(), open(c.id)) }, c.title),
        el("span", { class: "muted" }, `${c.message_count / 2} question(s), ${ukDateTime(c.last_message_at)}`),
        el("button", { type: "button", class: "secondary", onclick: () => remove(c.id) }, "Delete"),
      ),
    ),
  );
}

async function open(id) {
  const c = await guard(message, () => api.get(`${base}/conversations/${id}`));
  if (!c) return;
  conversationId = c.id;
  thread.replaceChildren(...c.messages.map(bubble));
}

async function remove(id) {
  if (!confirm("Delete this conversation? This cannot be undone.")) return;
  await guard(message, () => api.delete(`${base}/conversations/${id}`));
  if (conversationId === id) newConversation();
  loadPast();
}

async function loadSettings() {
  const s = await guard(message, () => api.get(`${base}/settings`));
  if (!s) return;
  const enabled = checkbox("ai_enabled", "The assistant is switched on for this business", { checked: s.ai_enabled, disabled: !isOwner });
  const outside = checkbox("allow_external_ai", "Allow an outside AI provider to reword answers", { checked: s.allow_external_ai, disabled: !isOwner || !s.external_provider_available });
  const improve = checkbox("allow_model_improvement", "Allow my conversations to be used to improve AI models", { checked: s.allow_model_improvement, disabled: !isOwner });
  const save = el("button", { type: "button" }, "Save");
  save.addEventListener("click", async () => {
    const saved = await guard(message, () =>
      api.put(`${base}/settings`, {
        ai_enabled: enabled.querySelector("input").checked,
        allow_external_ai: outside.querySelector("input").checked,
        allow_model_improvement: improve.querySelector("input").checked,
      }),
    );
    if (saved) {
      message.hidden = true;
      save.textContent = "Saved";
      setTimeout(() => (save.textContent = "Save"), 1500);
    }
  });
  settingsBox.replaceChildren(
    el("p", { class: "muted" }, s.external_provider_available ? "By default nothing leaves Vyterlix: answers are put together from your own results. If you allow it, only the question and the short list of facts found for it are sent to the outside provider, never your records, and what comes back is checked against those facts before you see it." : "No outside AI provider is set up on this installation, so every answer is put together by Vyterlix itself and nothing leaves it."),
    enabled,
    outside,
    improve,
    isOwner ? el("div", { class: "actions" }, save) : el("p", { class: "muted" }, "Only the owner can change these."),
  );
}

const opened = await openBusiness(message);
if (opened) await start(opened);
