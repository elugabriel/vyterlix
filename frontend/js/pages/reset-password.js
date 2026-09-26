import { api } from "../auth.js";
import { el } from "../dom.js";
import { bindForm, showMessage, takeTokenFromUrl } from "../ui.js";

const form = document.getElementById("form");
const message = document.getElementById("message");
const token = takeTokenFromUrl();

if (!token) {
  form.hidden = true;
  showMessage(message, "error", "This page needs the link from your password reset email.");
}

bindForm(form, message, async ({ new_password, confirm }) => {
  if (new_password !== confirm) {
    showMessage(message, "error", "The two passwords don't match.");
    return;
  }
  const res = await api.post("/auth/reset-password", { token, new_password }, { auth: false });
  form.hidden = true;
  showMessage(message, "success", res.message);
  message.append(" ", el("a", { href: "login.html" }, "Log in"));
});
