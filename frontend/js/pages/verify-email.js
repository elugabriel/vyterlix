import { ApiError } from "../api.js";
import { api, getSessionUser, updateCachedUser } from "../auth.js";
import { bindForm, showMessage, takeTokenFromUrl } from "../ui.js";

const message = document.getElementById("message");
const resendForm = document.getElementById("resend-form");
const token = takeTokenFromUrl();

async function verify() {
  showMessage(message, "info", "Verifying your email…");
  try {
    const user = await api.post("/auth/verify-email", { token }, { auth: false });
    showMessage(message, "success", "Thanks, your email is verified.");
    if (await getSessionUser()) updateCachedUser(user);
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
    resendForm.hidden = false;
  }
}

if (token) {
  await verify();
} else {
  resendForm.hidden = false;
  const user = await getSessionUser();
  if (user) resendForm.elements.email.value = user.email;
}

bindForm(resendForm, message, async ({ email }) => {
  const res = await api.post("/auth/resend-verification", { email: email.trim() }, { auth: false });
  showMessage(message, "success", res.message);
});
