// The public front page. It is plain text and works without this script; the script only
// (1) opens and closes the menu on a phone, and (2) if you are already signed in, swaps
// "Log in / Get started" for "Open your dashboard". It never sends anyone away from the page.

import { getSessionUser } from "../auth.js";

const header = document.getElementById("l-header");
const burger = document.getElementById("l-burger");

function setMenu(open) {
  header.classList.toggle("open", open);
  burger.setAttribute("aria-expanded", String(open));
}

burger.addEventListener("click", () => setMenu(!header.classList.contains("open")));
document.getElementById("l-nav").addEventListener("click", (event) => {
  if (event.target.closest("a")) setMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && header.classList.contains("open")) {
    setMenu(false);
    burger.focus();
  }
});

try {
  if (await getSessionUser()) {
    for (const link of document.querySelectorAll('[data-session="start"]')) {
      link.textContent = "Open your dashboard";
      link.href = "app.html";
    }
    for (const link of document.querySelectorAll('[data-session="login"]')) {
      link.hidden = true;
    }
  }
} catch {
  // Not signed in, or the server is busy: the page stays as it is.
}
