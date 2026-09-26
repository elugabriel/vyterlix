// Small UI helpers shared by all pages. All text goes in via textContent / el(), never
// innerHTML, so API data can't inject markup.

import { ApiError } from "./api.js";
import { el } from "./dom.js";

/** Show a message box: type is "error", "success" or "info". Pass "" to hide it. */
export function showMessage(box, type, text) {
  box.hidden = !text;
  box.className = `message message-${type}`;
  box.replaceChildren(text ?? "");
}

function clearFieldErrors(form) {
  for (const node of form.querySelectorAll(".field-error")) node.remove();
  for (const input of form.querySelectorAll("[aria-invalid]")) input.removeAttribute("aria-invalid");
}

function showFieldErrors(form, details) {
  let shown = false;
  for (const detail of Array.isArray(details) ? details : []) {
    const name = detail.loc?.at(-1);
    const input = typeof name === "string" && form.elements.namedItem(name);
    if (!input || input.getAttribute("aria-invalid")) continue;
    input.setAttribute("aria-invalid", "true");
    input.after(el("p", { class: "field-error" }, friendlyValidation(detail)));
    shown = true;
  }
  return shown;
}

function friendlyValidation(detail) {
  const min = detail.ctx?.min_length;
  const max = detail.ctx?.max_length;
  if (detail.type === "string_too_short" && min) return `Must be at least ${min} characters.`;
  if (detail.type === "string_too_long" && max) return `Must be at most ${max} characters.`;
  if (detail.type === "value_error" && String(detail.msg).includes("email")) {
    return "Enter a valid email address.";
  }
  return String(detail.msg ?? "Invalid value").replace(/^Value error, /, "");
}

/**
 * Wire up a form: on submit, disable its button, run `handler(values)`, and show any
 * API error in `messageBox` (and next to the offending fields for validation errors).
 */
export function bindForm(form, messageBox, handler) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    clearFieldErrors(form);
    showMessage(messageBox, "info", "");
    button.disabled = true;
    try {
      await handler(Object.fromEntries(new FormData(form)));
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      const onFields = err.code === "validation_error" && showFieldErrors(form, err.details);
      showMessage(messageBox, "error", onFields ? "Please fix the highlighted fields." : err.message);
    } finally {
      button.disabled = false;
    }
  });
}

/**
 * Read a one-time token from the page URL, then remove it from the address bar and
 * browser history so it isn't left behind or leaked to other sites.
 */
export function takeTokenFromUrl() {
  const params = new URLSearchParams(location.search);
  const token = params.get("token");
  if (token) {
    params.delete("token");
    const rest = params.toString();
    history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
  }
  return token;
}
