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
};

/** Why a KPI shows no number, in plain words. `requires` is what the KPI needs (from the API). */
export function missingText(value, requires = []) {
  if (value.status === "no_data") {
    const needs = requires.map((r) => NEEDS[r] ?? r);
    return needs.length ? `Needs ${needs.join(" and ")}, which you haven't added yet.` : "Needs records you haven't added yet.";
  }
  return "Can't be worked out for this period (for example, nothing was sold, or there is no earlier period to compare with).";
}

/** A small bar chart of a KPI over time (finished periods solid, the one in progress faded). */
export function barChart(values, unit, label) {
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
  const zero = height - ((0 - min) / span) * (height - 8);
  const bars = shown.map((v, i) => {
    const n = Number(v.value);
    const y = height - ((n - min) / span) * (height - 8);
    const top = Math.min(y, zero);
    const bar = svg("rect", {
      x: i * (barWidth + gap),
      y: top,
      width: barWidth,
      height: Math.max(1, Math.abs(zero - y)),
      class: v.is_complete ? "bar" : "bar bar-partial",
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
  return el("div", { class: "chart-box" }, chart);
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
