// Your account: where you are signed in. Each browser or phone is a device that can be named and
// signed out, so a lost phone or a borrowed computer can be cut off from here.

import { ApiError } from "../api.js";
import { api, logout, requireLogin } from "../auth.js";
import { el } from "../dom.js";
import { ukDateTime } from "../format.js";
import { input } from "../forms.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const list = document.getElementById("devices");

const user = await requireLogin();
document.getElementById("user-name").textContent = user.full_name;
document.getElementById("logout").addEventListener("click", async () => {
  await logout();
  location.assign("login.html");
});

/** The name to show: the one it was given, else what kind of thing it is. */
function label(s) {
  if (s.device_name) return s.device_name;
  if (s.client === "mobile") return "A phone";
  const agent = s.user_agent ?? "";
  const browser = /Edg\//.test(agent) ? "Edge" : /Firefox\//.test(agent) ? "Firefox" : /Chrome\//.test(agent) ? "Chrome" : /Safari\//.test(agent) ? "Safari" : "A web browser";
  return /Windows/.test(agent) ? `${browser} on Windows` : /Mac OS/.test(agent) ? `${browser} on a Mac` : /Android/.test(agent) ? `${browser} on Android` : /iPhone|iPad/.test(agent) ? `${browser} on an iPhone or iPad` : browser;
}

async function run(work) {
  try {
    await work();
    message.hidden = true;
    await load();
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
  }
}

function row(s) {
  const name = el("strong", {}, label(s));
  const holder = el("div", {});
  const rename = () => {
    const box = input("device_name", { value: s.device_name ?? "", maxlength: 100, placeholder: "For example: Jo's iPhone" });
    const save = el("button", { type: "button" }, "Save name");
    const cancel = el("button", { type: "button", class: "secondary" }, "Cancel");
    save.addEventListener("click", () => {
      const value = box.value.trim();
      if (!value) return showMessage(message, "error", "Type a name first.");
      run(() => api.patch(`/me/sessions/${s.id}`, { device_name: value }));
    });
    cancel.addEventListener("click", () => holder.replaceChildren(buttons));
    holder.replaceChildren(el("div", { class: "actions" }, box, save, cancel));
    box.focus();
  };
  const buttons = el(
    "div",
    { class: "actions" },
    el("button", { type: "button", class: "secondary", onclick: rename }, "Give it a name"),
    s.current ? null : el("button", { type: "button", class: "secondary", onclick: () => confirm(`Sign out ${label(s)}? It will have to log in again.`) && run(() => api.delete(`/me/sessions/${s.id}`)) }, "Sign it out"),
  );
  holder.append(buttons);
  return el(
    "div",
    { class: "kpi" },
    el("div", {}, name, " ", s.current ? el("span", { class: "badge health-healthy" }, "This device") : null, " ", el("span", { class: "muted" }, s.client === "mobile" ? "Phone app" : "Website")),
    el("p", { class: "muted" }, `Signed in ${ukDateTime(s.created_at)}${s.last_used_at ? `. Last used ${ukDateTime(s.last_used_at)}` : ""}.`),
    holder,
  );
}

async function load() {
  const sessions = await api.get("/me/sessions");
  const others = sessions.filter((s) => !s.current).length;
  list.replaceChildren(
    ...sessions.map(row),
    others
      ? el("div", { class: "actions" }, el("button", { type: "button", class: "secondary", onclick: () => confirm(`Sign out the other ${others} ${others === 1 ? "device" : "devices"}?`) && run(() => api.post("/me/sessions/revoke-others")) }, "Sign out of every other device"))
      : el("p", { class: "muted" }, "You are only signed in here."),
  );
}

// Start last, so every const above has been set up before the page first runs.
await load();
