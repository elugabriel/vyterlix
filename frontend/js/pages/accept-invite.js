import { ApiError } from "../api.js";
import { api, getSessionUser, logout } from "../auth.js";
import { el } from "../dom.js";
import { showMessage, takeTokenFromUrl } from "../ui.js";

// The invitee may need to log in or register first. Keep the invitation token for this
// tab only (sessionStorage) while they do, and forget it once it's used or invalid.
const STORE_KEY = "vyterlix.invite";
const message = document.getElementById("message");
const box = document.getElementById("invite");
const summary = document.getElementById("invite-summary");
const actions = document.getElementById("invite-actions");

const fromUrl = takeTokenFromUrl();
if (fromUrl) sessionStorage.setItem(STORE_KEY, fromUrl);
const token = sessionStorage.getItem(STORE_KEY);

function fail(text) {
  sessionStorage.removeItem(STORE_KEY);
  showMessage(message, "error", text);
}

async function main() {
  if (!token) return fail("This page needs the link from your invitation email.");

  let invite;
  try {
    invite = await api.post("/invitations/preview", { token }, { auth: false });
  } catch (err) {
    if (err instanceof ApiError) return fail(err.message);
    throw err;
  }

  const who = invite.invited_by ? `${invite.invited_by} has` : "You've been";
  summary.replaceChildren(
    `${who} invited you to join `,
    el("strong", {}, invite.organization_name),
    ` as ${invite.role === "owner" ? "an" : "a"} ${invite.role}. The invitation is for `,
    el("strong", {}, invite.email),
    ".",
  );
  box.hidden = false;

  const user = await getSessionUser();
  const back = encodeURIComponent("accept-invite.html");
  const email = encodeURIComponent(invite.email);

  if (!user) {
    actions.replaceChildren(
      el("p", { class: "muted" }, "Log in or create an account with that email to accept."),
      el("div", {},
        el("a", { class: "button", href: `login.html?next=${back}&email=${email}` }, "Log in"),
        " ",
        el("a", { class: "button secondary", href: `register.html?next=${back}&email=${email}` },
          "Create account"),
      ),
    );
    return;
  }

  if (user.email !== invite.email) {
    actions.replaceChildren(
      el("p", {}, `You're logged in as ${user.email}. Log out, then log in as ${invite.email}.`),
      el("button", {
        type: "button",
        class: "secondary",
        onclick: async () => {
          await logout();
          location.assign(`login.html?next=${back}&email=${email}`);
        },
      }, "Log out"),
    );
    return;
  }

  const accept = el("button", { type: "button" }, `Join ${invite.organization_name}`);
  accept.addEventListener("click", async () => {
    accept.disabled = true;
    try {
      const res = await api.post("/invitations/accept", { token });
      sessionStorage.removeItem(STORE_KEY);
      box.hidden = true;
      showMessage(message, "success", `You've joined ${res.organization.name}.`);
      message.append(" ", el("a", { href: "app.html" }, "Go to your businesses"));
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      showMessage(message, "error", err.message);
      accept.disabled = false;
    }
  });
  actions.replaceChildren(accept);
}

await main();
