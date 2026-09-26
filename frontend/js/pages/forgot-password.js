import { api } from "../auth.js";
import { bindForm, showMessage } from "../ui.js";

const form = document.getElementById("form");
const message = document.getElementById("message");

bindForm(form, message, async ({ email }) => {
  const res = await api.post("/auth/forgot-password", { email: email.trim() }, { auth: false });
  // Same answer whether or not the account exists, by design.
  showMessage(message, "success", res.message);
  form.reset();
});
