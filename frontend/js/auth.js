// Session handling for every page.
//
// - The access token lives ONLY in this module's memory, never in browser storage, so
//   injected scripts or other tabs can't read it from there.
// - After a page load the memory is empty, so we ask /auth/refresh: the browser sends the
//   httpOnly refresh cookie (JavaScript can't read it) and we get a fresh access token.
// - Tokens are refreshed shortly before they expire, and once more if the API says one
//   has expired anyway.

import { createApiClient } from "./api.js";

let accessToken = null;
let currentUser = null;
let refreshTimer = null;
let refreshInFlight = null;

export const api = createApiClient({
  baseUrl: globalThis.VYTERLIX_CONFIG?.apiBaseUrl,
  getAccessToken: () => accessToken,
  onExpired: () => refreshSession(),
});

function setSession(tokens) {
  accessToken = tokens.access_token;
  currentUser = tokens.user;
  clearTimeout(refreshTimer);
  // Refresh a minute before expiry (but never sooner than 10 seconds from now).
  const inMs = Math.max(10, tokens.expires_in - 60) * 1000;
  refreshTimer = setTimeout(() => refreshSession(), inMs);
}

function clearSession() {
  accessToken = null;
  currentUser = null;
  clearTimeout(refreshTimer);
}

/** Get a new access token using the refresh cookie. Resolves true/false. */
export function refreshSession() {
  // Several callers at once share one request (the refresh token rotates on every use).
  refreshInFlight ??= api
    .post("/auth/refresh", undefined, { auth: false, retry: false })
    .then((tokens) => (setSession(tokens), true))
    .catch(() => (clearSession(), false))
    .finally(() => (refreshInFlight = null));
  return refreshInFlight;
}

export async function login(email, password) {
  setSession(await api.post("/auth/login", { email, password }, { auth: false }));
  return currentUser;
}

export async function logout() {
  try {
    await api.post("/auth/logout", undefined, { auth: false });
  } finally {
    clearSession();
  }
}

/** The logged-in user, restoring the session after a page load if possible; else null. */
export async function getSessionUser() {
  if (currentUser) return currentUser;
  return (await refreshSession()) ? currentUser : null;
}

/** Update the cached user after e.g. email verification or a profile change. */
export function updateCachedUser(user) {
  currentUser = user;
}

/** For pages that need a login: returns the user, or sends them to the login page. */
export async function requireLogin() {
  const user = await getSessionUser();
  if (user) return user;
  const next = location.pathname.split("/").pop() + location.search;
  location.replace(`login.html?next=${encodeURIComponent(next)}`);
  return new Promise(() => {}); // the page is navigating away
}

/**
 * Where to go after logging in. Only plain page names in this app are allowed (e.g.
 * "app.html" or "accept-invite.html?x=1"), so a crafted ?next= link can't send users
 * to another site after they log in.
 */
export function safeNext(fallback = "app.html") {
  const next = new URLSearchParams(location.search).get("next") ?? "";
  return /^[a-z0-9-]+\.html(\?[^#]*)?$/i.test(next) ? next : fallback;
}
