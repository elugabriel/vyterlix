// Shared by the pages that deal with actions: the words used for each status, who work can be given
// to, and the form that turns a recommendation into an action.

import { api } from "./auth.js";
import { el } from "./dom.js";
import { field, input, orNull, select } from "./forms.js";

export const STATUS_CLASS = {
  pending: "health-fair",
  accepted: "health-fair",
  in_progress: "health-healthy",
  partially_completed: "health-fair",
  completed: "health-healthy",
  cancelled: "health-not_enough_data",
  overdue: "health-at_risk",
};

export function statusBadge(action) {
  return el("span", { class: `badge ${STATUS_CLASS[action.status] ?? "plain"}` }, action.status_label);
}

/** The people work can be given to, as [id, name] pairs. Anyone who cannot see the team list gets just themselves. */
export async function loadPeople(orgId, me) {
  try {
    const members = await api.get(`/organizations/${orgId}/members`);
    return members.filter((m) => m.status === "active").map((m) => [m.user_id, m.full_name]);
  } catch {
    return [[me.id, me.full_name]];
  }
}

/** The form shown when accepting a recommended option: change what you like, then accept. */
export function acceptForm(option, people, me, { onAccept, onCancel }) {
  const title = input("title", { value: option.title, maxlength: 200, required: true });
  const description = el("textarea", { name: "description", rows: 3 });
  description.value = option.description;
  const steps = el("textarea", { name: "steps", rows: 5 });
  steps.value = option.intervention.steps.join("\n");
  const owner = select("owner", people, { selected: me.id });
  const start = input("start_date", { type: "date" });
  const target = input("target_date", { type: "date" });
  const note = input("note", { maxlength: 500 });
  const submit = el("button", { type: "submit" }, "Accept");
  const form = el(
    "form",
    { class: "stack" },
    field("What we will do", title),
    field("Details", description),
    field("Steps (one per line)", steps, "Take out the ones that do not suit you, or add your own."),
    field("Who will do it", owner),
    field("Start date", start, "Leave blank to start today."),
    field("Finish by", target, `Leave blank to allow about ${option.days_to_effect} days.`),
    field("A note (optional)", note),
    el("div", { class: "actions" }, submit, el("button", { type: "button", class: "secondary", onclick: onCancel }, "Cancel")),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      await onAccept({
        option_id: option.id,
        title: title.value,
        description: description.value,
        steps: steps.value.split("\n").map((t) => t.trim()).filter(Boolean).map((text) => ({ text })),
        owner_user_id: owner.value || null,
        start_date: orNull(start.value),
        target_date: orNull(target.value),
        note: orNull(note.value),
      });
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}
