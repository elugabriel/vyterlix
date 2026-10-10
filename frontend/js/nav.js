// The side menu shown on every page of a business, so there is one way around the app and the same
// words for every screen. It is built by openBusiness() (business.js), so a page cannot forget it.
// Who can see what is decided by the server; every person sees every link, and the pages
// themselves only offer buttons to people who may use them. On a narrow screen it becomes a row
// of links across the top.

import { api } from "./auth.js";
import { el } from "./dom.js";

// [page, words, icon, group]
export const NAV = [
  ["dashboard.html", "Today", "today", "Overview"],
  ["changes.html", "What changed", "changes", "Overview"],
  ["actions.html", "Actions", "actions", "Overview"],
  ["forecast.html", "Forecast", "forecast", "Insight"],
  ["kpis.html", "Key figures", "kpis", "Insight"],
  ["health.html", "Health", "health", "Insight"],
  ["reports.html", "Reports", "reports", "Insight"],
  ["alerts.html", "Alerts", "alerts", "Help and memory"],
  ["assistant.html", "Ask Vyterlix", "assistant", "Help and memory"],
  ["memory.html", "What we know", "memory", "Help and memory"],
  ["data.html", "Your data", "data", "Your business"],
  ["business.html", "Settings", "settings", "Your business"],
  ["billing.html", "Billing", "billing", "Your business"],
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

// Simple line icons (24 by 24, drawn with a stroke)
const ICONS = {
  today: ["M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  changes: ["M3 12h4l3-8 4 16 3-8h4"],
  actions: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M8.5 12.5l2.5 2.5 4.5-5"],
  forecast: ["M3 17l6-6 4 4 8-8", "M15 7h6v6"],
  kpis: ["M5 21V10", "M12 21V3", "M19 21v-7"],
  health: ["M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"],
  reports: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5", "M9 13h6", "M9 17h6"],
  alerts: ["M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9", "M13.7 21a2 2 0 0 1-3.4 0"],
  assistant: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z", "M19 17l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"],
  memory: ["M9 18h6", "M10 21h4", "M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"],
  data: ["M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6", "M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"],
  settings: ["M4 6h10", "M18 6h2", "M4 12h2", "M10 12h10", "M4 18h12", "M20 18h0", "M14 4v4", "M8 10v4", "M18 16v4"],
  billing: ["M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M3 10h18"],
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name] ?? []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export function currentPage() {
  return location.pathname.split("/").pop() || "index.html";
}

/** Build the menu. `unread` is how many notifications are waiting (shown on the bell link). */
export function buildNav(orgId, page = currentPage(), unread = 0, orgName = "") {
  const active = BELONGS_TO[page] ?? page;
  const parts = [
    el("a", { class: "brand", href: "app.html" }, "Vyterlix"),
    el("div", { class: "side-org" }, el("div", { class: "label" }, "Business"), el("div", { class: "name" }, orgName || "Your business"), el("a", { href: "app.html" }, "Switch business")),
  ];
  let group = "";
  for (const [file, label, glyph, section] of NAV) {
    if (section !== group) {
      group = section;
      parts.push(el("div", { class: "nav-title" }, section));
    }
    parts.push(el("a", { href: `${file}?org=${orgId}`, class: `nav-link${file === active ? " active" : ""}`, "aria-current": file === active ? "page" : null }, icon(glyph), label));
  }
  parts.push(
    el("div", { class: "spacer" }),
    el("a", { href: `notifications.html?org=${orgId}`, class: `nav-link bell${page === "notifications.html" ? " active" : ""}`, id: "nav-notifications" }, icon("alerts"), "Notifications", unread ? el("span", { class: "count" }, String(unread)) : null),
  );
  return el("nav", { class: "sidenav", "aria-label": "This business" }, ...parts);
}

/** Put the menu in the page (once), then fill in how many notifications are waiting. */
export function mountNav(orgId, orgName = "") {
  if (document.getElementById("subnav")) return;
  const bar = buildNav(orgId, currentPage(), 0, orgName);
  bar.id = "subnav";
  document.body.classList.add("shell");
  const top = document.querySelector(".topbar");
  if (top) top.after(bar);
  else document.body.prepend(bar);
  api
    .get(`/organizations/${orgId}/notifications?limit=1`)
    .then((box) => {
      if (box.unread) document.getElementById("nav-notifications").append(el("span", { class: "count" }, String(box.unread)));
    })
    .catch(() => {});
}
