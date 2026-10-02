// Where another system sends the person back after they approve (or refuse) access.
// The address carries a one-time code; read it, remove it from the address bar at once, and
// hand it to the API, which checks it really belongs to this person's connect attempt.

import { ApiError } from "../api.js";
import { api, logout, requireLogin } from "../auth.js";
import { CONNECTING_KEY } from "../data.js";
import { el } from "../dom.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function backLink(orgId) {
  const href = orgId ? `connections.html?org=${orgId}` : "app.html";
  return el("a", { class: "button", href }, orgId ? "Back to Connections" : "Go to your businesses");
}

function readStoredOrg() {
  try {
    const id = sessionStorage.getItem(CONNECTING_KEY) ?? "";
    sessionStorage.removeItem(CONNECTING_KEY);
    return UUID.test(id) ? id : null;
  } catch {
    return null;
  }
}

async function finish() {
  const user = await requireLogin();
  document.getElementById("user-name").textContent = user.full_name;
  document.getElementById("logout").addEventListener("click", async () => {
    await logout();
    location.assign("login.html");
  });

  // Take everything out of the address bar before doing anything else.
  const params = new URLSearchParams(location.search);
  const code = params.get("code");
  const state = params.get("state");
  const refused = params.get("error");
  history.replaceState(null, "", location.pathname);
  const orgId = readStoredOrg();

  if (refused) {
    showMessage(message, "info", "The connection wasn't approved, so nothing was connected.");
    content.replaceChildren(el("div", { class: "actions" }, backLink(orgId)));
    return;
  }
  if (!code || !state || !orgId) {
    showMessage(message, "error", "This page only finishes a connection that was started from Connections. Please start again.");
    content.replaceChildren(el("div", { class: "actions" }, backLink(orgId)));
    return;
  }
  content.replaceChildren(el("p", { class: "muted", role: "status" }, "Finishing your connection…"));
  try {
    const connection = await api.post(`/organizations/${orgId}/integrations/callback`, { state, code });
    showMessage(message, "success", `Connected: ${connection.display_name}.`);
    content.replaceChildren(
      el("p", {}, "You can now fetch its data from the Connections page."),
      el("div", { class: "actions" }, backLink(orgId)),
    );
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
    content.replaceChildren(el("div", { class: "actions" }, backLink(orgId)));
  }
}

// Start last, so every const above has been set up before the page first runs.
await finish();
