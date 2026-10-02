// Type in data by hand: sales, expenses, customers, suppliers, products and stock.
// Vyterlix works without any file or connection; everything here can also be imported.

import { ApiError } from "../api.js";
import { api } from "../auth.js";
import { openBusiness } from "../business.js";
import { dataNav, number, requireDataManager, table } from "../data.js";
import { el } from "../dom.js";
import { gbp, ukDate } from "../format.js";
import { field, input, orNull, select } from "../forms.js";
import { bindForm, showMessage } from "../ui.js";

const message = document.getElementById("message");
const content = document.getElementById("content");

const TABS = [
  ["sale", "Sale"],
  ["expense", "Expense"],
  ["customer", "Customer"],
  ["supplier", "Supplier"],
  ["product", "Product"],
  ["stock", "Stock"],
];
const VAT_CHOICES = [
  ["20", "20% VAT (standard)"],
  ["5", "5% VAT (reduced)"],
  ["0", "0% (no VAT)"],
  ["amount", "I'll type the VAT amount"],
];
const STOCK_KINDS = [
  ["delivery", "Delivery in"],
  ["opening", "Opening stock (what you started with)"],
  ["return", "Customer return"],
  ["sale", "Sold or used"],
  ["write_off", "Written off (damaged, out of date)"],
  ["adjustment", "Stock count correction"],
];

let orgId = null;
let tab = "sale";
let choices = null;


async function start({ org }) {
  orgId = org.id;
  document.getElementById("org-name").textContent = org.name;
  document.getElementById("nav").replaceChildren(dataNav(orgId, "entry"));
  if (!requireDataManager(org, message)) return;
  choices = await loadChoices();
  await render();
}

const path = (rest) => `/organizations/${orgId}/${rest}`;

async function loadChoices() {
  const pick = (page) => page.items.map((i) => [i.id, i.name ?? i.email]);
  const listed = (items) => items.filter((i) => i.is_active).map((i) => [i.id, i.name]);
  const [customers, suppliers, products, channels, categories, ctypes, offerings] = await Promise.all([
    api.get(path("customers?limit=100")),
    api.get(path("suppliers?limit=100")),
    api.get(path("products?limit=100")),
    api.get(path("lists/sales_channel")),
    api.get(path("lists/cost_category")),
    api.get(path("lists/customer_type")),
    api.get(path("lists/offering")),
  ]);
  return {
    customers: pick(customers),
    suppliers: pick(suppliers),
    products: pick(products),
    channels: listed(channels),
    categories: listed(categories),
    ctypes: listed(ctypes),
    offerings: listed(offerings),
    productNames: Object.fromEntries(pick(products)),
  };
}

const todayUk = () => new Date().toLocaleDateString("en-CA", { timeZone: "Europe/London" });

async function render() {
  const tabs = el(
    "div",
    { class: "tabs", role: "tablist" },
    ...TABS.map(([key, label]) => {
      const button = el("button", { type: "button", role: "tab", "aria-selected": key === tab ? "true" : "false" }, label);
      button.addEventListener("click", async () => {
        tab = key;
        await render();
      });
      return button;
    }),
  );
  const builder = { sale, expense, customer, supplier, product, stock }[tab];
  const { form, recent } = builder();
  const box = el("div", { class: "message", role: "alert", hidden: true });
  const listBox = el("div", {});
  const label = TABS.find(([key]) => key === tab)[1];
  content.replaceChildren(
    tabs,
    el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, `Add a ${label.toLowerCase()}`), box, form.node),
    el("section", { class: "card" }, el("h2", { style: "margin-top:0" }, recent.title), listBox),
  );
  bindForm(form.node, box, async (values) => {
    await form.submit(values);
    const refreshNeeded = ["customer", "supplier", "product"].includes(tab);
    if (refreshNeeded) choices = await loadChoices();
    await render();
    showMessage(message, "success", `${label} saved.`);
  });
  await showRecent(recent, listBox);
}

// --- shared pieces -----------------------------------------------------------------------------------

const SOURCE = { manual: "Typed in", csv: "CSV file", excel: "Excel file" };
const kindText = { sale: "Sale", refund: "Refund", expense: "Expense", credit: "Credit note" };

function dateField(name, label) {
  return field(label, input(name, { type: "date", value: todayUk(), required: true }));
}

/** Amount + VAT questions shared by sales and expenses. */
function moneyFields() {
  const amount = input("amount", { type: "number", step: "0.01", min: "0.01", required: true });
  const includes = select("amount_includes_vat", [["yes", "Yes, it includes VAT"], ["no", "No, it's before VAT"]], { selected: "yes" });
  const vat = select("vat_choice", VAT_CHOICES, { selected: "20" });
  const vatAmount = input("vat_amount", { type: "number", step: "0.01", min: "0" });
  const vatAmountField = field("VAT amount (£)", vatAmount);
  vatAmountField.hidden = true;
  vat.addEventListener("change", () => (vatAmountField.hidden = vat.value !== "amount"));
  const node = el(
    "div",
    {},
    el("div", { class: "row" }, field("Amount (£)", amount), field("Does that amount include VAT?", includes)),
    el("div", { class: "row" }, field("VAT", vat), vatAmountField),
  );
  return {
    node,
    read(values) {
      const body = { amount: values.amount, amount_includes_vat: values.amount_includes_vat === "yes" };
      if (values.vat_choice === "amount") body.vat_amount = values.vat_amount || "0";
      else body.vat_rate = values.vat_choice;
      return body;
    },
  };
}

function deleteControl(url, onDone) {
  const holder = el("span", {});
  const ask = el("button", { type: "button", class: "link" }, "Delete");
  function reset() {
    holder.replaceChildren(ask);
  }
  ask.addEventListener("click", () => {
    const yes = el("button", { type: "button", class: "link" }, "Yes, delete");
    const no = el("button", { type: "button", class: "link" }, "No");
    no.addEventListener("click", reset);
    yes.addEventListener("click", async () => {
      try {
        await api.delete(url);
        await onDone();
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        showMessage(message, "error", err.message);
        reset();
      }
    });
    holder.replaceChildren("Sure? ", yes, " · ", no);
  });
  reset();
  return holder;
}

async function showRecent(recent, into) {
  const page = await api.get(path(`${recent.path}?limit=8`));
  into.replaceChildren(
    table(
      recent.headers,
      page.items.map((item) => [...recent.cells(item), deleteControl(path(`${recent.path}/${item.id}`), render)]),
      { empty: "Nothing added yet." },
    ),
    page.total > 8 ? el("p", { class: "muted" }, `Showing the first 8 of ${number(page.total)}.`) : null,
  );
}

const optionalId = (v) => orNull(v);

// --- sale --------------------------------------------------------------------------------------------------

function sale() {
  const money = moneyFields();
  const lines = lineEditor();
  const node = el(
    "form",
    { novalidate: true },
    el("div", { class: "row" }, dateField("sold_on", "Date of sale"), field("What kind?", select("kind", [["sale", "Sale"], ["refund", "Refund"]]))),
    money.node,
    el(
      "div",
      { class: "row" },
      field("Customer (optional)", select("customer_id", choices.customers, { placeholder: "No customer" })),
      field("Sales channel (optional)", select("sales_channel_id", choices.channels, { placeholder: "Not set" })),
    ),
    el("div", { class: "row" }, field("Reference (optional)", input("reference", { maxlength: 200 }), "e.g. an invoice or receipt number. Each can only be used once."), field("Notes (optional)", input("notes", { maxlength: 2000 }))),
    lines.node,
    el("button", { type: "submit" }, "Save sale"),
  );
  return {
    form: {
      node,
      submit: (v) =>
        api.post(path("sales"), {
          sold_on: v.sold_on,
          kind: v.kind,
          ...money.read(v),
          customer_id: optionalId(v.customer_id),
          sales_channel_id: optionalId(v.sales_channel_id),
          reference: orNull(v.reference),
          notes: orNull(v.notes),
          lines: lines.read(),
        }),
    },
    recent: {
      path: "sales",
      title: "Latest sales (newest first)",
      headers: ["Date", "Reference", "Total", "Type", "Source", ""],
      cells: (s) => [ukDate(s.sold_on), s.reference ?? "–", gbp(s.gross_amount), kindText[s.kind], SOURCE[s.source] ?? s.source],
    },
  };
}

/** Optional "what was sold" lines. They must add up to the sale's amount before VAT. */
function lineEditor() {
  const rows = el("div", {});
  const total = el("p", { class: "inline-note" }, "");
  const add = el("button", { type: "button", class: "secondary" }, "Add what was sold");
  const node = el(
    "fieldset",
    { class: "subform" },
    el("legend", {}, "What was sold (optional)"),
    el("p", { class: "hint" }, "Lets Vyterlix find your best sellers and margins. The amounts must add up to the sale's amount before VAT."),
    rows,
    total,
    add,
  );
  function recount() {
    const sum = [...rows.querySelectorAll("[data-net]")].reduce((acc, i) => acc + (Number(i.value) || 0), 0);
    total.textContent = rows.children.length ? `Lines add up to ${gbp(sum)} before VAT.` : "";
  }
  function addRow() {
    const net = input("line_net", { type: "number", step: "0.01", min: "0" });
    net.dataset.net = "1";
    net.addEventListener("input", recount);
    const row = el(
      "div",
      { class: "line-row" },
      field("Product", select("line_product", choices.products, { placeholder: "Not in my list" })),
      field("Or describe it", input("line_description", { maxlength: 300 })),
      field("How many", input("line_quantity", { type: "number", step: "0.01", min: "0.01", value: "1" })),
      field("Amount before VAT (£)", net),
      field("Cost to you (£, optional)", input("line_cost", { type: "number", step: "0.01", min: "0" })),
      el("button", { type: "button", class: "link" }, "Remove"),
    );
    row.querySelector("button").addEventListener("click", () => {
      row.remove();
      recount();
    });
    rows.append(row);
    recount();
  }
  add.addEventListener("click", addRow);
  return {
    node,
    read() {
      const out = [];
      for (const row of rows.children) {
        const get = (name) => row.querySelector(`[name=${name}]`).value;
        out.push({
          product_id: orNull(get("line_product")),
          description: orNull(get("line_description")),
          quantity: get("line_quantity"),
          net_amount: get("line_net") || "0",
          cost_amount: orNull(get("line_cost")),
        });
      }
      return out;
    },
  };
}

// --- expense -------------------------------------------------------------------------------------------------

function expense() {
  const money = moneyFields();
  const node = el(
    "form",
    { novalidate: true },
    el("div", { class: "row" }, dateField("spent_on", "Date"), field("What kind?", select("kind", [["expense", "Expense"], ["credit", "Credit note (money back)"]]))),
    money.node,
    el(
      "div",
      { class: "row" },
      field("Supplier (optional)", select("supplier_id", choices.suppliers, { placeholder: "No supplier" })),
      field("Cost category (optional)", select("cost_category_id", choices.categories, { placeholder: "Not set" })),
    ),
    el("div", { class: "row" }, field("What it was for (optional)", input("description", { maxlength: 300 })), field("Reference (optional)", input("reference", { maxlength: 200 }))),
    el("button", { type: "submit" }, "Save expense"),
  );
  return {
    form: {
      node,
      submit: (v) =>
        api.post(path("expenses"), {
          spent_on: v.spent_on,
          kind: v.kind,
          ...money.read(v),
          supplier_id: optionalId(v.supplier_id),
          cost_category_id: optionalId(v.cost_category_id),
          description: orNull(v.description),
          reference: orNull(v.reference),
        }),
    },
    recent: {
      path: "expenses",
      title: "Latest expenses (newest first)",
      headers: ["Date", "What for", "Total", "Type", "Source", ""],
      cells: (e) => [ukDate(e.spent_on), e.description ?? e.reference ?? "–", gbp(e.gross_amount), kindText[e.kind], SOURCE[e.source] ?? e.source],
    },
  };
}

// --- customer, supplier, product -------------------------------------------------------------------------------

function customer() {
  const node = el(
    "form",
    { novalidate: true },
    el("div", { class: "row" }, field("Name", input("name", { maxlength: 200 })), field("Email", input("email", { type: "email", maxlength: 320 }))),
    el("div", { class: "row" }, field("Postcode (optional)", input("postcode", { maxlength: 8 }), "A UK postcode, e.g. LS1 4AP"), field("Customer type (optional)", select("customer_type_id", choices.ctypes, { placeholder: "Not set" }))),
    el("p", { class: "hint" }, "Give a name or an email address, or both. Only add what you need."),
    el("button", { type: "submit" }, "Save customer"),
  );
  return {
    form: {
      node,
      submit: (v) =>
        api.post(path("customers"), {
          name: orNull(v.name),
          email: orNull(v.email),
          postcode: orNull(v.postcode),
          customer_type_id: optionalId(v.customer_type_id),
        }),
    },
    recent: {
      path: "customers",
      title: "Your customers (A to Z)",
      headers: ["Name", "Email", "Postcode", "Source", ""],
      cells: (c) => [c.name ?? "–", c.email ?? "–", c.postcode ?? "–", SOURCE[c.source] ?? c.source],
    },
  };
}

function supplier() {
  const node = el("form", { novalidate: true }, field("Supplier name", input("name", { maxlength: 200, required: true })), el("button", { type: "submit" }, "Save supplier"));
  return {
    form: { node, submit: (v) => api.post(path("suppliers"), { name: v.name }) },
    recent: { path: "suppliers", title: "Your suppliers (A to Z)", headers: ["Name", "Source", ""], cells: (s) => [s.name, SOURCE[s.source] ?? s.source] },
  };
}

function product() {
  const node = el(
    "form",
    { novalidate: true },
    el("div", { class: "row" }, field("Product name", input("name", { maxlength: 200, required: true })), field("Product code (optional)", input("sku", { maxlength: 100 }), "Your stock code. Each can only be used once.")),
    el(
      "div",
      { class: "row" },
      field("Selling price before VAT (£)", input("unit_price_ex_vat", { type: "number", step: "0.01", min: "0" })),
      field("What it costs you (£)", input("unit_cost", { type: "number", step: "0.01", min: "0" })),
      field("VAT rate", select("vat_rate", [["20", "20%"], ["5", "5%"], ["0", "0%"]], { placeholder: "Not set" })),
    ),
    field("Category (optional)", select("offering_id", choices.offerings, { placeholder: "Not set" })),
    el("button", { type: "submit" }, "Save product"),
  );
  return {
    form: {
      node,
      submit: (v) =>
        api.post(path("products"), {
          name: v.name,
          sku: orNull(v.sku),
          offering_id: optionalId(v.offering_id),
          unit_price_ex_vat: orNull(v.unit_price_ex_vat),
          unit_cost: orNull(v.unit_cost),
          vat_rate: orNull(v.vat_rate),
        }),
    },
    recent: {
      path: "products",
      title: "Your products (A to Z)",
      headers: ["Name", "Code", "Price (before VAT)", "Source", ""],
      cells: (p) => [p.name, p.sku ?? "–", p.unit_price_ex_vat === null ? "–" : gbp(p.unit_price_ex_vat), SOURCE[p.source] ?? p.source],
    },
  };
}

// --- stock ---------------------------------------------------------------------------------------------------------------

function stock() {
  const kind = select("kind", STOCK_KINDS, { selected: "delivery" });
  const direction = select("direction", [["more", "Adds stock"], ["less", "Removes stock"]]);
  const directionField = field("Which way?", direction);
  directionField.hidden = true;
  kind.addEventListener("change", () => (directionField.hidden = kind.value !== "adjustment"));
  const node = el(
    "form",
    { novalidate: true },
    el("div", { class: "row" }, field("Product", select("product_id", choices.products, { placeholder: "Choose a product" })), dateField("moved_on", "Date")),
    el("div", { class: "row" }, field("What happened?", kind), directionField, field("How many?", input("quantity", { type: "number", step: "0.01", min: "0", required: true }))),
    el("div", { class: "row" }, field("Cost of each (£, optional)", input("unit_cost", { type: "number", step: "0.01", min: "0" })), field("Notes (optional)", input("notes", { maxlength: 300 }))),
    choices.products.length ? null : el("p", { class: "hint" }, "Add a product first (the Product tab)."),
    el("button", { type: "submit" }, "Save stock change"),
  );
  return {
    form: {
      node,
      submit: (v) => {
        // People type how many; the direction follows from what happened.
        const negative = ["sale", "write_off"].includes(v.kind) || (v.kind === "adjustment" && v.direction === "less");
        return api.post(path("stock-movements"), {
          product_id: v.product_id,
          moved_on: v.moved_on,
          kind: v.kind,
          quantity: `${negative ? "-" : ""}${v.quantity}`,
          unit_cost: orNull(v.unit_cost),
          notes: orNull(v.notes),
        });
      },
    },
    recent: {
      path: "stock-movements",
      title: "Latest stock changes (newest first)",
      headers: ["Date", "Product", "What happened", "Quantity", ""],
      cells: (m) => [
        ukDate(m.moved_on),
        choices.productNames[m.product_id] ?? "Unknown product",
        STOCK_KINDS.find(([k]) => k === m.kind)?.[1] ?? m.kind,
        String(Number(m.quantity)),
      ],
    },
  };
}

// Start last, so every const above has been set up before the page first runs.
const opened = await openBusiness(message);
if (opened) await start(opened);
