// Connections: link the business to another system (accounting, online shop...) so its data
// can be fetched automatically. Shows what we would read BEFORE the person connects.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import {
  CONNECTING_KEY,
  dataNav,
  guard,
  jobProgress,
  number,
  put,
  requireDataManager,
  table,
  waitForJob,
} from "../data.js";
import { el } from "../dom.js";
import { ukDateTime } from "../format.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const STATUS = {
  connected: ["Connected", "status-imported"],
  needs_reauth: ["Needs you to sign in again", "status-failed"],
  disconnected: ["Disconnected", "status-undone"],
};

let orgId = null;
let base = "";

async function start({ org }) {
  orgId = org.id;
  base = `/organizations/${orgId}/integrations`;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("nav").replaceChildren(dataNav(orgId, "connections"));
  if (!requireDataManager(org, message)) return;
  await load();
}

async function load() {
  const [providers, connections] = await Promise.all([
    guard(message, () => api.get(`${base}/providers`)),
    guard(message, () => api.get(base)),
  ]);
  if (!providers || !connections) return;
  content.replaceChildren(connectionsCard(connections), addCard(providers, connections));
}

// --- what is connected ---------------------------------------------------------------------------

function connectionsCard(connections) {
  const card = el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, "Connected systems"));
  if (!connections.length) {
    card.append(el("p", { class: "muted" }, "Nothing is connected yet. Add a connection below."));
    return card;
  }
  card.append(...connections.map(connectionBlock));
  return card;
}

function connectionBlock(c) {
  const [statusText, statusClass] = STATUS[c.status] ?? [c.status, ""];
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const progress = jobProgress("Fetching your data");
  progress.node.hidden = true;
  const actions = el("div", { class: "actions", style: "margin:8px 0 0" });

  if (c.status === "connected") {
    const sync = el("button", { type: "button" }, "Update now");
    sync.addEventListener("click", () => syncNow(c, sync, box, progress));
    actions.append(sync, disconnectControl(c, box));
  } else if (c.status === "needs_reauth") {
    const again = el("button", { type: "button" }, "Sign in again");
    again.addEventListener("click", () => connect(c.provider, c.id, again, box));
    actions.append(again, disconnectControl(c, box));
  } else {
    const back = el("button", { type: "button" }, "Connect again");
    back.addEventListener("click", () => connect(c.provider, null, back, box));
    actions.append(back);
  }

  const lastLine = c.last_successful_sync_at
    ? `Last updated ${ukDateTime(c.last_successful_sync_at)}.`
    : c.status === "disconnected"
      ? `Disconnected on ${ukDateTime(c.disconnected_at)}. What was imported stays in your data.`
      : "Not updated yet.";

  return el(
    "div",
    { class: "connection" },
    el("h3", {}, c.display_name, " ", el("span", { class: `badge ${statusClass}` }, statusText)),
    el("p", { class: "muted" }, lastLine),
    c.needs_attention && c.last_error_message
      ? el("div", { class: "message message-error" }, c.last_error_message)
      : null,
    permissionsList(c.permissions),
    box,
    progress.node,
    actions,
    syncHistory(c),
  );
}

function permissionsList(permissions) {
  if (!permissions.length) return null;
  return el(
    "details",
    {},
    el("summary", {}, "What we can read"),
    el("ul", {}, ...permissions.map((p) => el("li", {}, p))),
    el("p", { class: "muted" }, "We only read. We never change anything in the other system."),
  );
}

function syncHistory(c) {
  const holder = el("div", {});
  const details = el("details", {}, el("summary", {}, "Recent updates"), holder);
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    const syncs = await guard(message, () => api.get(`${base}/${c.id}/syncs`));
    if (!syncs) return;
    holder.replaceChildren(
      table(
        ["Started", "Result", "Records found", "Note"],
        syncs.map((s) => [
          ukDateTime(s.started_at),
          { succeeded: "Worked", failed: "Failed", running: "Running" }[s.status] ?? s.status,
          number(s.records_fetched),
          s.error_message ?? "",
        ]),
        { empty: "No updates yet." },
      ),
    );
  });
  return details;
}

// --- actions -------------------------------------------------------------------------------------

async function syncNow(c, button, box, progress) {
  button.disabled = true;
  showMessage(box, "info", "");
  progress.node.hidden = false;
  try {
    const job = await api.post(`${base}/${c.id}/sync`);
    const done = await waitForJob(orgId, job, progress);
    await load();
    showMessage(message, "success", `Updated ${c.display_name}: ${number(done.result?.records_fetched ?? 0)} records found.`);
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    await load();
    showMessage(message, "error", err.message);
  }
}

function disconnectControl(c, box) {
  const holder = el("span", {});
  const ask = el("button", { type: "button", class: "secondary" }, "Disconnect");
  function showAsk() {
    holder.replaceChildren(ask);
  }
  ask.addEventListener("click", () => {
    const yes = el("button", { type: "button", class: "danger" }, "Yes, disconnect");
    const no = el("button", { type: "button", class: "secondary" }, "Cancel");
    no.addEventListener("click", showAsk);
    yes.addEventListener("click", async () => {
      yes.disabled = no.disabled = true;
      const done = await guard(box, () => api.post(`${base}/${c.id}/disconnect`));
      if (done) {
        await load();
        showMessage(message, "success", `${c.display_name} is disconnected. What it already brought in stays in your data.`);
      } else {
        yes.disabled = no.disabled = false;
      }
    });
    holder.replaceChildren(
      el("span", {}, "Stop fetching data from this system? "),
      yes,
      " ",
      no,
    );
  });
  showAsk();
  return holder;
}

/** Ask the API where to send the person, remember which business, and go. */
async function connect(provider, integrationId, button, box) {
  button.disabled = true;
  showMessage(box, "info", "");
  const body = { provider, ...(integrationId ? { integration_id: integrationId } : {}) };
  const started = await guard(box, () => api.post(`${base}/connect`, body));
  if (!started) {
    button.disabled = false;
    return;
  }
  const target = new URL(started.authorize_url, location.href);
  if (!["https:", "http:"].includes(target.protocol)) {
    showMessage(box, "error", "That connection address isn't safe to open.");
    button.disabled = false;
    return;
  }
  try {
    sessionStorage.setItem(CONNECTING_KEY, orgId);
  } catch {
    // Private browsing can refuse storage; the callback page then asks the person to start again.
  }
  location.assign(target.href);
}

// --- adding one ----------------------------------------------------------------------------------

function addCard(providers, connections) {
  const card = el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, "Add a connection"));
  const live = new Set(connections.filter((c) => c.status !== "disconnected").map((c) => c.provider));
  if (!providers.length) {
    card.append(el("p", { class: "muted" }, "No other systems can be connected yet."));
    return card;
  }
  for (const p of providers) {
    const box = el("div", { class: "message", role: "alert", hidden: true });
    const button = el("button", { type: "button" }, live.has(p.key) ? "Connect another account" : "Connect");
    button.addEventListener("click", () => connect(p.key, null, button, box));
    put(
      card,
      el(
        "div",
        { class: "connection" },
        el("h3", {}, p.label),
        el("p", { class: "muted" }, "If you connect, Vyterlix will be able to:"),
        el("ul", {}, ...p.permissions.map((text) => el("li", {}, text))),
        el("p", { class: "muted" }, "Read only. You can disconnect at any time."),
        box,
        el("div", { class: "actions", style: "margin:8px 0 0" }, button),
      ),
    );
  }
  return card;
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
