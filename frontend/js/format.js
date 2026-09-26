// UK-first display helpers: £ amounts, dd/mm/yyyy dates, UK region and month names.

export const UK_REGIONS = [
  ["north_east", "North East"],
  ["north_west", "North West"],
  ["yorkshire_and_the_humber", "Yorkshire and the Humber"],
  ["east_midlands", "East Midlands"],
  ["west_midlands", "West Midlands"],
  ["east_of_england", "East of England"],
  ["london", "London"],
  ["south_east", "South East"],
  ["south_west", "South West"],
  ["wales", "Wales"],
  ["scotland", "Scotland"],
  ["northern_ireland", "Northern Ireland"],
];

export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const pounds = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });
const plain = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 });

/** "1234.5" -> "£1,234.50" (the API sends exact decimals as strings). */
export function gbp(value) {
  return pounds.format(Number(value));
}

/** Format a goal/benchmark amount in its unit: £, % or a plain count. */
export function amount(value, unit) {
  if (value === null || value === undefined) return "";
  if (unit === "gbp") return gbp(value);
  if (unit === "percent") return `${plain.format(Number(value))}%`;
  return plain.format(Number(value));
}

/** "2027-03-31" -> "31/03/2027" */
export function ukDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

export function regionName(code) {
  return UK_REGIONS.find(([c]) => c === code)?.[1] ?? "";
}
