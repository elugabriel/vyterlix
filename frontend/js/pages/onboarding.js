import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { el } from "../dom.js";
import { SECTION_RENDERERS } from "../onboarding-sections.js";
import { showMessage } from "../ui.js";

const message = document.getElementById("message");
const steps = document.getElementById("steps");
const panel = document.getElementById("panel");
const progressBar = document.getElementById("progress");
const progressText = document.getElementById("progress-text");
const STATUS_LABEL = { done: "Done", to_do: "To do", skipped: "Skipped" };

const opened = await openBusiness(message);
if (opened) await start(opened);

async function start({ org }) {
  const orgId = org.id;
  const isOwner = org.role === "owner";
  document.getElementById("org-name").textContent = `Set up ${org.name}`;
  let state = null;
  let current = new URLSearchParams(location.search).get("section");

  async function refresh() {
    state = await api.get(`/organizations/${orgId}/onboarding`);
    progressBar.max = state.total;
    progressBar.value = state.done;
    progressText.textContent = `${state.done} of ${state.total} done`;
    steps.replaceChildren(
      ...state.sections.map((s) =>
        el(
          "li",
          { class: s.key === current ? "current" : "" },
          el(
            "button",
            { type: "button", class: "step", onclick: () => show(s.key) },
            el("span", {}, s.title, s.required ? " *" : ""),
            el("span", { class: `badge status-${s.status}` }, STATUS_LABEL[s.status]),
          ),
        ),
      ),
    );
  }

  function sectionAfter(key) {
    const keys = state.sections.map((s) => s.key);
    return keys[keys.indexOf(key) + 1] ?? null;
  }

  async function goNext(fromKey) {
    await refresh();
    const next = fromKey ? sectionAfter(fromKey) : state.next_section;
    await (next ? show(next) : showFinish());
  }

  async function show(key) {
    current = key;
    history.replaceState(null, "", `?org=${orgId}&section=${key}`);
    const section = state.sections.find((s) => s.key === key);
    await refresh();
    panel.replaceChildren(el("h2", {}, section.title), el("p", { class: "muted" }, section.hint));

    if (!isOwner) {
      panel.append(el("p", {}, "Only an owner can change the business setup."));
      return;
    }
    const body = el("div");
    panel.append(body);
    const render = () =>
      SECTION_RENDERERS[key]({
        panel: body,
        orgId,
        done: () => goNext(key),
        rerender: async () => {
          body.replaceChildren();
          await render();
          await refresh();
        },
      });
    try {
      await render();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      showMessage(message, "error", err.message);
    }

    if (!section.required && section.status === "to_do") {
      panel.append(
        el(
          "button",
          {
            type: "button",
            class: "secondary skip",
            onclick: async () => {
              await api.post(`/organizations/${orgId}/onboarding/skip`, { section: key });
              await goNext(key);
            },
          },
          "Skip for now",
        ),
      );
    }
  }

  async function showFinish() {
    current = null;
    history.replaceState(null, "", `?org=${orgId}`);
    await refresh();
    panel.replaceChildren(el("h2", {}, "All set"));
    if (!state.ready_for_dashboard) {
      panel.append(el("p", {}, "Add your business details to finish setting up."));
      return;
    }
    const left = state.sections.filter((s) => s.status !== "done").length;
    panel.append(
      el(
        "p",
        {},
        left
          ? `You can finish now and come back to the ${left} remaining section${left === 1 ? "" : "s"} any time.`
          : "Everything's set up.",
      ),
    );
    if (!isOwner) return;
    const finish = el("button", { type: "button" }, "Finish setup");
    finish.addEventListener("click", async () => {
      finish.disabled = true;
      try {
        await api.post(`/organizations/${orgId}/onboarding/complete`);
        location.assign(`business.html?org=${orgId}`);
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        showMessage(message, "error", err.message);
        finish.disabled = false;
      }
    });
    panel.append(finish);
  }

  await refresh();
  if (current && state.sections.some((s) => s.key === current)) {
    await show(current);
  } else if (state.next_section) {
    await show(state.next_section);
  } else {
    await showFinish();
  }
}
