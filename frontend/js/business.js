// Shared by pages that work inside one business (?org=<id>).

import { ApiError } from "./api.js";
import { api, logout, requireLogin } from "./auth.js";
import { showMessage } from "./ui.js";

/** The business id from ?org=, only if it looks like a real id (it goes into API paths). */
export function orgIdFromUrl() {
  const id = new URLSearchParams(location.search).get("org") ?? "";
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id : null;
}

/** Log in if needed, fill the top bar, and load the business. Returns { user, org } or null. */
export async function openBusiness(message) {
  const user = await requireLogin();
  document.getElementById("user-name").textContent = user.full_name;
  document.getElementById("logout").addEventListener("click", async () => {
    await logout();
    location.assign("login.html");
  });

  const orgId = orgIdFromUrl();
  if (!orgId) {
    showMessage(message, "error", "This link doesn't point to a business.");
    return null;
  }
  try {
    const org = await api.get(`/organizations/${orgId}`);
    document.title = `${org.name} · Vyterlix`;
    return { user, org };
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    showMessage(message, "error", err.message);
    return null;
  }
}
