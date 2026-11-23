import { getSessionUser, login, safeNext } from "../auth.js";
import { bindForm } from "../ui.js";

const form = document.getElementById("form");
const message = document.getElementById("message");
const params = new URLSearchParams(location.search);

if (params.get("email")) form.elements.email.value = params.get("email");
// Keep ?next= when switching to registration, e.g. while accepting an invitation.
if (params.get("next")) {
  document.getElementById("register-link").href = `register.html?${params}`;
}

bindForm(form, message, async ({ email, password }) => {
  await login(email.trim(), password);
  location.assign(safeNext());
});

// Already logged in (e.g. came back with the browser's back button)? Carry on. This runs after the form is
// usable, so a slow connection never leaves the person unable to type and press the button.
getSessionUser().then((user) => {
  if (user) location.replace(safeNext());
});
