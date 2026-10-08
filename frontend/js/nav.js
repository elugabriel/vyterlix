// The navigation bar shown under the top bar on every page of a business, so there is one way
// around the app and the same words for every screen. It is built by openBusiness() (business.js),
// so a page cannot forget it. Who can see what is decided by the server; every person sees every
// link, and the pages themselves only offer buttons to people who may use them.

import { api } from "./auth.js";
import { el } from "./dom.js";

export const NAV = [
  ["dashboard.html", "Today"],
  ["changes.html", "What changed"],
  ["actions.html", "Actions"],
  ["forecast.html", "Forecast"],
  ["kpis.html", "Key figures"],
  ["health.html", "Health"],
  ["alerts.html", "Alerts"],
  ["assistant.html", "Ask Vyterlix"],
  ["memory.html", "What we know"],
  ["data.html", "Your data"],
  ["business.html", "Settings"],
];
// Screens that belong under another entry, so that entry stays lit while you are on them
const BELONGS_TO = {
  "imports.html": "data.html",
  "import.html": "data.html",
  "entry.html": "data.html",
  "connections.html": "data.html",
  "integrations-callback.html": "data.html",
  "onboarding.html": "business.html",
  "notifications.html": "alerts.html",
};

export function currentPage() {
  return location.pathname.split("/").pop() || "index.html";
}

/** Build the bar. `unread` is how many notifications are waiting (shown on a bell link). */
export function buildNav(orgId, page = currentPage(), unread = 0) {
  const active = BELONGS_TO[page] ?? page;
  const links = NAV.map(([file, label]) => el("a", { href: `${file}?org=${orgId}`, class: file === active ? "active" : "", "aria-current": file === active ? "page" : null }, label));
  const bell = el("a", { href: `notifications.html?org=${orgId}`, class: `bell${page === "notifications.html" ? " active" : ""}`, id: "nav-notifications" }, unread ? `Notifications (${unread})` : "Notifications");
  return el("nav", { class: "subnav", "aria-label": "This business" }, ...links, bell);
}

/** Put the bar in the page (once), then fill in how many notifications are waiting. */
export function mountNav(orgId) {
  if (document.getElementById("subnav")) return;
  const bar = buildNav(orgId);
  bar.id = "subnav";
  const top = document.querySelector(".topbar");
  if (top) top.after(bar);
  else document.body.prepend(bar);
  api
    .get(`/organizations/${orgId}/notifications?limit=1`)
    .then((box) => {
      if (box.unread) document.getElementById("nav-notifications").textContent = `Notifications (${box.unread})`;
    })
    .catch(() => {});
}
