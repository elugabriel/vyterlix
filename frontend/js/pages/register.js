import { api, login, safeNext } from "../auth.js";
import { bindForm } from "../ui.js";

const form = document.getElementById("form");
const message = document.getElementById("message");
const params = new URLSearchParams(location.search);

if (params.get("email")) form.elements.email.value = params.get("email");
if (params.get("next")) document.getElementById("login-link").href = `login.html?${params}`;

bindForm(form, message, async ({ full_name, email, password }) => {
  await api.post("/auth/register", { full_name, email: email.trim(), password }, { auth: false });
  // Log straight in (limited access until the email is verified).
  await login(email.trim(), password);
  location.assign(safeNext());
});
