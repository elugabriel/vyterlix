import { ApiError } from "../api.js";
import { api, logout, requireLogin } from "../auth.js";
import { el } from "../dom.js";
import { bindForm, showMessage } from "../ui.js";

const message = document.getElementById("message");
const orgList = document.getElementById("orgs");
const orgsEmpty = document.getElementById("orgs-empty");
const createForm = document.getElementById("create-form");
const banner = document.getElementById("verify-banner");

const user = await requireLogin();
document.getElementById("user-name").textContent = user.full_name;

document.getElementById("logout").addEventListener("click", async () => {
  await logout();
  location.assign("login.html");
});

function renderOrgs(orgs) {
  orgList.replaceChildren(
    ...orgs.map((org) =>
      el("li", {}, el("span", {}, org.name), el("span", { class: "badge" }, org.role)),
    ),
  );
  orgsEmpty.hidden = orgs.length > 0;
}

async function loadOrgs() {
  try {
    renderOrgs(await api.get("/organizations"));
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
  }
}

if (user.email_verified) {
  await loadOrgs();
} else {
  // Limited access until the email is verified.
  banner.hidden = false;
  document.getElementById("verify-text").textContent =
    `Please verify your email address: we sent a link to ${user.email}. ` +
    "You can create and join businesses once it's verified.";
  orgsEmpty.hidden = false;
  for (const input of createForm.elements) input.disabled = true;

  const resend = document.getElementById("resend");
  resend.addEventListener("click", async () => {
    resend.disabled = true;
    try {
      const res = await api.post("/auth/resend-verification", { email: user.email }, { auth: false });
      showMessage(message, "success", res.message);
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      showMessage(message, "error", err.message);
    } finally {
      resend.disabled = false;
    }
  });
}

bindForm(createForm, message, async ({ name }) => {
  const org = await api.post("/organizations", { name });
  createForm.reset();
  showMessage(message, "success", `Created ${org.name}.`);
  await loadOrgs();
});
