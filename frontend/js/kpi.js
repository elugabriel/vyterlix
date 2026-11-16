// Showing KPI values: formatting by unit, period names, change arrows and a tiny chart.
// The API sends numbers as decimal strings, already rounded for their unit.

import { el } from "./dom.js";
import { gbp, MONTHS, ukDate } from "./format.js";

const SVG = "http://www.w3.org/2000/svg";

export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  node.append(...children);
  return node;
}

/** "140.00" + "gbp" -> "£140.00"; "60.00" + "percent" -> "60.0%"; counts get thousands commas. */
export function formatValue(value, unit) {
  if (value === null || value === undefined) return "–";
  const n = Number(value);
  if (unit === "gbp") return gbp(n);
  if (unit === "percent") return `${n.toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
  if (unit === "ratio") return `${n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} times`;
  return n.toLocaleString("en-GB", { maximumFractionDigits: 2 });
}

/** "2026-02-01" + "month" -> "February 2026"; weeks -> "week of 09/03/2026". */
export function periodLabel(start, granularity) {
  const [y, m] = start.split("-").map(Number);
  if (granularity === "month") return `${MONTHS[m - 1]} ${y}`;
  if (granularity === "quarter") return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
  if (granularity === "year") return String(y);
  return `week of ${ukDate(start)}`;
}

/** What changed since the previous period, in words: "up 50.0% on the month before". */
export function changeText(value, unit, granularity) {
  if (value.status !== "ok" || value.previous_value === null) return null;
  const before = { month: "the month before", week: "the week before", quarter: "the quarter before", year: "the year before" }[granularity];
  if (unit === "percent") {
    const points = Number(value.value) - Number(value.previous_value);
    if (points === 0) return { sign: 0, text: `no change on ${before}` };
    return { sign: Math.sign(points), text: `${points > 0 ? "up" : "down"} ${Math.abs(points).toFixed(1)} points on ${before}` };
  }
  if (value.change_pct === null) return { sign: 0, text: `was ${formatValue(value.previous_value, unit)} ${before}` };
  const pct = Number(value.change_pct);
  if (pct === 0) return { sign: 0, text: `no change on ${before}` };
  return { sign: Math.sign(pct), text: `${pct > 0 ? "up" : "down"} ${Math.abs(pct).toFixed(1)}% on ${before}` };
}

/** What a figure means, in everyday words: the sentence, why it matters, and a caution if the data is thin. */
export function plainBlock(plain, { short = false } = {}) {
  if (!plain) return null;
  return el(
    "div",
    { class: `plain plain-${plain.tone}` },
    el("p", { class: "plain-says" }, plain.says),
    short ? null : el("p", { class: "plain-why" }, plain.why),
    plain.caution ? el("p", { class: "plain-caution" }, plain.caution) : null,
  );
}

/** CSS class for a change: green when it is the good direction, red when not, grey otherwise. */
export function changeClass(sign, direction) {
  if (sign === 0 || direction === "neutral") return "muted";
  return (sign > 0) === (direction === "up_good") ? "status-ok" : "status-bad";
}

const NEEDS = {
  sales: "sales",
  expenses: "expenses",
  customer_sales: "sales that say which customer bought",
  stock: "stock records",
  bookings: "appointments",
  suppliers: "costs that say who you bought from",
  ads: "your adverts",
  timed: "appointment times",
  staff_hours: "hours worked",
};

/** Why a KPI shows no number, in plain words. `requires` is what the KPI needs (from the API). */
export function missingText(value, requires = []) {
  if (value.status === "no_data") {
    const needs = requires.map((r) => NEEDS[r] ?? r);
    return needs.length ? `Needs ${needs.join(" and ")}, which you haven't added yet.` : "Needs records you haven't added yet.";
  }
  return "Can't be worked out for this period (for example, nothing was sold, or there is no earlier period to compare with).";
}

/** A short number for the top of a bar: "£1.8k", "55", "22%". */
function shortValue(value, unit) {
  const n = Number(value);
  if (unit === "gbp") return Math.abs(n) >= 1000 ? `${n < 0 ? "-" : ""}£${(Math.abs(n) / 1000).toFixed(1)}k` : gbp(n).replace(/\.00$/, "");
  if (unit === "percent") return `${Math.round(n)}%`;
  return Math.abs(n) >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n * 10) / 10);
}

/** A bar chart of a KPI over time: every bar has its month underneath and its value on top, so it can be
 * read without a sentence. Finished periods are solid, the one in progress faded, `marked` is picked out,
 * and `tone(v)` can colour a bar (health uses it). */
export function barChart(values, unit, label, { marked = null, tone = null } = {}) {
  const shown = values.filter((v) => v.status === "ok");
  const width = 560;
  const height = 120;
  if (!shown.length) return el("p", { class: "muted" }, "No figures to chart yet.");
  const numbers = shown.map((v) => Number(v.value));
  const max = Math.max(0, ...numbers);
  const min = Math.min(0, ...numbers);
  const span = max - min || 1;
  const gap = 4;
  const barWidth = Math.max(4, (width - gap * (shown.length - 1)) / shown.length);
  const yOf = (n) => height - ((n - min) / span) * (height - 24);
  const zero = yOf(0);
  const bars = shown.map((v, i) => {
    const y = yOf(Number(v.value));
    const colour = tone ? ` bar-${tone(v)}` : "";
    const bar = svg("rect", {
      x: i * (barWidth + gap),
      y: Math.min(y, zero),
      width: barWidth,
      height: Math.max(1, Math.abs(zero - y)),
      class: `${v.is_complete ? "bar" : "bar bar-partial"}${colour}${marked && v.period_start === marked ? " bar-marked" : ""}`,
      rx: 2,
    });
    bar.append(svg("title", {}, `${v.period_start}: ${formatValue(v.value, unit)}`));
    return bar;
  });
  const chart = svg(
    "svg",
    { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label, class: "chart", preserveAspectRatio: "none" },
    svg("line", { x1: 0, x2: width, y1: zero, y2: zero, class: "axis" }),
    ...bars,
  );
  const centre = (i) => ((i * (barWidth + gap) + barWidth / 2) / width) * 100;
  const monthName = (v) => MONTHS[Number(v.period_start.slice(5, 7)) - 1].slice(0, 3);
  const narrow = window.matchMedia("(max-width: 640px)").matches;
  const everyOther = shown.length > 12;
  const valuesThin = shown.length > (narrow ? 6 : 12);
  const isMarked = (v) => marked && v.period_start === marked;
  // Values sit just above (or, for a loss, just below) their bar; months sit under the chart.
  const tops = el(
    "div",
    { class: "chart-values", "aria-hidden": "true" },
    ...shown.map((v, i) => {
      const n = Number(v.value);
      const y = yOf(n);
      const style = n < 0 ? `left:${centre(i)}%;top:${(Math.max(y, zero) / height) * 100}%` : `left:${centre(i)}%;top:calc(${(y / height) * 100}% - 1.35em)`;
      return valuesThin && i % 2 !== shown.length % 2 && !isMarked(v) ? null : el("span", { class: `chart-value${isMarked(v) ? " chart-value-marked" : ""}`, style }, shortValue(v.value, unit));
    }),
  );
  const months = el(
    "div",
    { class: "chart-labels", "aria-hidden": "true" },
    ...shown.map((v, i) => (everyOther && i % 2 !== shown.length % 2 && !isMarked(v) ? null : el("span", { class: `chart-label${isMarked(v) ? " chart-label-marked" : ""}`, style: `left:${centre(i)}%` }, monthName(v)))),
  );
  const year = shown[0].period_start.slice(0, 4) === shown[shown.length - 1].period_start.slice(0, 4) ? shown[0].period_start.slice(0, 4) : `${shown[0].period_start.slice(0, 4)} to ${shown[shown.length - 1].period_start.slice(0, 4)}`;
  return el("div", { class: "chart-box" }, el("div", { class: "chart-frame" }, chart, tops), months, el("div", { class: "chart-year muted", "aria-hidden": "true" }, year));
}

/** One long bar split into coloured parts with the same words beneath: parts are [count, colour, words]. */
export function stackBar(parts, caption = "") {
  const shown = parts.filter(([n]) => n > 0);
  if (!shown.length) return null;
  const words = shown.map(([n, , w]) => `${n} ${w}`).join(" · ");
  return el(
    "div",
    { class: "outcome-bar-box" },
    el("div", { class: "outcome-bar", role: "img", "aria-label": words }, ...shown.map(([n, c]) => el("span", { class: c, style: `flex:${n}` }))),
    el("p", { class: "muted" }, caption ? `${caption} ${words}` : words),
  );
}

/** A tiny bar picture of the last few months, for a figure's tile. The newest finished month is orange. */
export function sparkline(values, label) {
  const shown = values.filter((v) => v.status === "ok").slice(-6);
  if (shown.length < 2) return null;
  const width = 120;
  const height = 36;
  const numbers = shown.map((v) => Number(v.value));
  const max = Math.max(0, ...numbers);
  const min = Math.min(0, ...numbers);
  const span = max - min || 1;
  const gap = 4;
  const barWidth = (width - gap * (shown.length - 1)) / shown.length;
  const lastFinished = shown.findLastIndex((v) => v.is_complete);
  const yOf = (n) => height - ((n - min) / span) * (height - 3);
  const zero = yOf(0);
  return svg(
    "svg",
    { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label, class: "spark" },
    ...shown.map((v, i) => svg("rect", { x: i * (barWidth + gap), y: Math.min(yOf(Number(v.value)), zero), width: barWidth, height: Math.max(1.5, Math.abs(zero - yOf(Number(v.value)))), rx: 2, class: i === lastFinished ? "bar bar-marked" : v.is_complete ? "bar" : "bar bar-partial" })),
  );
}

/** A thin horizontal bar with its percentage beside it, for "share of sales" and "budget used" cells.
 * With `over`, a bar past that mark turns red. */
export function shareBar(pct, { over = null } = {}) {
  const shown = Math.max(0, Math.min(100, pct));
  const tooMuch = over !== null && pct > over;
  return el(
    "span",
    { class: "share-bar" },
    el("span", { class: "share-track", "aria-hidden": "true" }, el("span", { class: tooMuch ? "share-fill share-over" : "share-fill", style: `width:${shown}%` })),
    el("span", { class: tooMuch ? "status-bad" : "" }, `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`),
  );
}

/** A line of past figures, then the forecast as a dashed line with its range shaded around it. Months
 * that have since finished show what really happened as a ring. `history` is [{period_start, value}],
 * `predictions` is [{period_start, value, lower, upper, actual_value}]. */
export function rangeChart(history, predictions, unit, label) {
  const width = 560;
  const height = 200;
  const pad = 10;
  const past = history.map((h) => Number(h.value));
  const future = predictions.map((p) => ({ value: Number(p.value), lower: Number(p.lower), upper: Number(p.upper), actual: p.actual_value === null ? null : Number(p.actual_value) }));
  if (!past.length || !future.length) return el("p", { class: "muted" }, "Nothing to chart yet.");
  const all = [...past, ...future.flatMap((f) => [f.lower, f.upper, f.actual ?? f.value])];
  const min = Math.min(0, ...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const count = past.length + future.length;
  const x = (i) => pad + (i / (count - 1)) * (width - pad * 2);
  const y = (n) => height - pad - ((n - min) / span) * (height - pad * 2);
  const last = past.length - 1;
  const pastPoints = past.map((n, i) => `${x(i)},${y(n)}`).join(" ");
  const joined = [`${x(last)},${y(past[last])}`, ...future.map((f, i) => `${x(last + 1 + i)},${y(f.value)}`)].join(" ");
  const upper = [`${x(last)},${y(past[last])}`, ...future.map((f, i) => `${x(last + 1 + i)},${y(f.upper)}`)];
  const lower = [`${x(last)},${y(past[last])}`, ...future.map((f, i) => `${x(last + 1 + i)},${y(f.lower)}`)].reverse();
  const dots = future.flatMap((f, i) => {
    const forecastDot = svg("circle", { cx: x(last + 1 + i), cy: y(f.value), r: 3.5, class: "dot" }, svg("title", {}, `${periodLabel(predictions[i].period_start, "month")}: ${formatValue(predictions[i].value, unit)} (${formatValue(predictions[i].lower, unit)} to ${formatValue(predictions[i].upper, unit)})`));
    if (f.actual === null) return [forecastDot];
    const ring = svg("circle", { cx: x(last + 1 + i), cy: y(f.actual), r: 5, class: "ring" }, svg("title", {}, `What happened in ${periodLabel(predictions[i].period_start, "month")}: ${formatValue(predictions[i].actual_value, unit)}`));
    return [forecastDot, ring];
  });
  const chart = svg(
    "svg",
    { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label, class: "chart chart-tall", preserveAspectRatio: "none" },
    svg("line", { x1: 0, x2: width, y1: y(0), y2: y(0), class: "axis" }),
    svg("polygon", { points: [...upper, ...lower].join(" "), class: "band" }),
    svg("polyline", { points: pastPoints, class: "line" }),
    svg("polyline", { points: joined, class: "line line-forecast" }),
    ...dots,
  );
  return el("div", { class: "chart-box" }, chart);
}
