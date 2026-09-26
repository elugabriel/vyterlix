// Small builders for form controls, so every page lays out fields the same way.
// Everything is created with el(), so values from the API are never parsed as HTML.

import { el } from "./dom.js";

let uid = 0;
const nextId = (name) => `f-${name}-${++uid}`;

/** A labelled field wrapper: <div class="field"><label/>{control}<p class="hint"/></div> */
export function field(label, control, hint) {
  if (!control.id) control.id = nextId(control.name || "x");
  return el(
    "div",
    { class: "field" },
    el("label", { for: control.id }, label),
    control,
    hint ? el("p", { class: "hint" }, hint) : null,
  );
}

export function input(name, { type = "text", value = "", ...attrs } = {}) {
  const node = el("input", { name, type, ...attrs });
  if (value !== null && value !== undefined) node.value = String(value);
  return node;
}

/** options: [[value, label], ...]; pass placeholder to add an empty first choice. */
export function select(name, options, { selected = "", placeholder = null, ...attrs } = {}) {
  const node = el(
    "select",
    { name, ...attrs },
    placeholder !== null ? el("option", { value: "" }, placeholder) : null,
    ...options.map(([value, label]) => el("option", { value: String(value) }, label)),
  );
  node.value = selected === null || selected === undefined ? "" : String(selected);
  return node;
}

export function checkbox(name, label, { checked = false, ...attrs } = {}) {
  const box = el("input", { type: "checkbox", name, ...attrs });
  box.checked = Boolean(checked);
  return el("label", { class: "check" }, box, " ", label);
}

/** "" -> null, otherwise the trimmed string (for optional text fields). */
export function orNull(value) {
  const text = String(value ?? "").trim();
  return text === "" ? null : text;
}

/** "" -> null, otherwise a whole number. */
export function intOrNull(value) {
  const text = String(value ?? "").trim();
  return text === "" ? null : Number.parseInt(text, 10);
}
