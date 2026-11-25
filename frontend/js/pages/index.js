// The public front page. It is plain text and works without this script; the script only opens and
// closes the menu on a phone. The header always offers "Log in" and "Create account", whoever is looking.

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
