"""A year of FAKE trading for an invented UK bakery, with every total known in advance.

Nothing here is real: the shop, the customers, the products and every figure are invented,
the emails use example.com and the postcodes are the unassigned "ZZ" ones. The same seed always
produces the same files, and the generator works out the totals as it goes, so a test can
import the files and check that exactly those totals arrive in the business's data.

It is a *clean* year (no gaps, no missing costs) so the KPI engine has solid ground to stand
on; the small files in sample-data/ are the ones with deliberate problems.

Shape of the year (1 Oct 2025 to 30 Sep 2026):
- shut on Sundays, 25 and 26 December and 1 January; busiest on Saturdays; a December peak
  and a January dip; a few refunds;
- a mix of shop, website and market-stall sales, about 4 in 10 attached to one of 400 customers
  who come back at very different rates (a few regulars, a long tail of one-offs);
- every sale has its cost (always a positive number, refunds included), every expense has a
  category and an invoice number;
- stock: an opening count, a delivery and a sales movement per product each week, and some
  write-offs, never going below zero.
"""

import csv
import io
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

PENNY = Decimal("0.01")
BUSINESS_NAME = "Fakeham Bakery (demo data)"
START, END = date(2025, 10, 1), date(2026, 9, 30)

# name, sku, price ex VAT, cost, VAT rate %, category, how often it sells (relative)
PRODUCTS = [
    ("Demo Sourdough Loaf", "DEMO-001", "4.20", "1.10", 0, "Bread", 10),
    ("Demo Seeded Batch", "DEMO-002", "3.60", "0.95", 0, "Bread", 6),
    ("Demo Baguette", "DEMO-003", "2.10", "0.55", 0, "Bread", 7),
    ("Demo Croissant", "DEMO-004", "1.90", "0.48", 0, "Pastry", 12),
    ("Demo Pain au Chocolat", "DEMO-005", "2.20", "0.60", 0, "Pastry", 9),
    ("Demo Cinnamon Swirl", "DEMO-006", "2.60", "0.70", 20, "Pastry", 8),
    ("Demo Victoria Sponge Slice", "DEMO-007", "3.10", "0.85", 20, "Cakes", 7),
    ("Demo Lemon Drizzle Slice", "DEMO-008", "3.10", "0.80", 20, "Cakes", 7),
    ("Demo Birthday Cake", "DEMO-009", "28.00", "7.50", 20, "Cakes", 1),
    ("Demo Flat White", "DEMO-010", "2.90", "0.45", 20, "Drinks", 14),
    ("Demo Filter Coffee", "DEMO-011", "2.20", "0.30", 20, "Drinks", 9),
    ("Demo Sausage Roll (hot)", "DEMO-012", "2.50", "0.90", 20, "Hot food", 8),
]
CHANNELS = ["Shop", "Website", "Market stall"]
CHANNEL_WEIGHTS = [60, 25, 15]
FAKE_POSTCODES = ["ZZ1 1AA", "ZZ1 2BB", "ZZ2 3CC", "ZZ3 4DD", "ZZ4 5EE", "ZZ5 6FF"]
# Busier and quieter months, and days of the week (Monday = 0).
MONTH_FACTOR = {10: 1.0, 11: 1.05, 12: 1.35, 1: 0.85, 2: 0.9, 3: 1.0, 4: 1.05, 5: 1.1, 6: 1.1,
                7: 1.0, 8: 0.95, 9: 1.0}  # fmt: skip
WEEKDAY_FACTOR = {0: 0.9, 1: 0.95, 2: 1.0, 3: 1.0, 4: 1.1, 5: 1.3}
CLOSED = {(12, 25), (12, 26), (1, 1)}
BASE_SALES_PER_DAY = 48
CUSTOMER_COUNT = 400

SALES_HEADER = ["Date", "Receipt No", "Item", "Qty", "Customer Email", "Channel",
                "Total (inc VAT)", "VAT", "Cost"]  # fmt: skip
EXPENSE_HEADER = ["Date", "Supplier", "Description", "Category", "Net", "VAT", "Invoice No"]
PRODUCT_HEADER = ["Name", "SKU", "Price", "Cost", "VAT rate", "Category"]
CUSTOMER_HEADER = ["Name", "Email", "Postcode", "Type"]
STOCK_HEADER = ["Date", "Product", "SKU", "Quantity", "Type", "Unit cost", "Reference"]

# What each file is and how amounts are written, in the order they must be imported
# (customers and products first, so sales and stock can point at them).
FILES = [
    ("milestone-customers.csv", "customers", {}),
    ("milestone-products.csv", "products", {"vat_inclusive": False}),
    ("milestone-sales.csv", "sales", {"vat_inclusive": True}),
    ("milestone-expenses.csv", "expenses", {"vat_inclusive": False}),
    ("milestone-stock.csv", "stock_movements", {}),
]


def money(value) -> Decimal:
    return Decimal(value).quantize(PENNY, rounding=ROUND_HALF_UP)


def pounds(value: Decimal) -> str:
    return f"£{value:,.2f}"


def uk(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")  # BOM, so Excel in the UK shows £ correctly


@dataclass
class Dataset:
    files: dict[str, bytes]
    expected: dict
    # filename -> (dataset kind, VAT options), in import order
    plan: list[tuple[str, str, dict]] = field(default_factory=lambda: list(FILES))

    def write(self, folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, content in self.files.items():
            (folder / name).write_bytes(content)
            paths.append(folder / name)
        (folder / "milestone-expected-totals.json").write_text(
            json.dumps(self.expected, indent=2), encoding="utf-8"
        )
        return paths


class _Totals:
    def __init__(self):
        self.rows = 0
        self.net = self.vat = self.gross = Decimal("0")

    def add(self, net: Decimal, vat: Decimal) -> None:
        self.rows += 1
        self.net += net
        self.vat += vat
        self.gross += net + vat

    def out(self) -> dict:
        return {"rows": self.rows, "net": str(self.net), "vat": str(self.vat),
                "gross": str(self.gross)}  # fmt: skip


def _trading_days() -> list[date]:
    days, day = [], START
    while day <= END:
        if day.weekday() != 6 and (day.month, day.day) not in CLOSED:
            days.append(day)
        day += timedelta(days=1)
    return days


def build(seed: int = 2026) -> Dataset:
    rng = random.Random(seed)

    # --- customers -----------------------------------------------------------------------------
    customers = [
        [f"Demo Customer {n:03d}", f"demo{n:03d}@example.com", rng.choice(FAKE_POSTCODES),
         "Trade" if rng.random() < 0.1 else "Retail"]
        for n in range(1, CUSTOMER_COUNT + 1)
    ]  # fmt: skip
    emails = [c[1] for c in customers]
    # A few regulars and a long tail: weight n falls off slowly.
    customer_weights = [1 / (n + 1) ** 0.6 for n in range(CUSTOMER_COUNT)]

    products = [
        [name, sku, pounds(Decimal(price)), pounds(Decimal(cost)), f"{rate}%", category]
        for name, sku, price, cost, rate, category, _ in PRODUCTS
    ]

    # --- sales ---------------------------------------------------------------------------------
    sales_rows: list[list[str]] = []
    month_totals: dict[str, _Totals] = defaultdict(_Totals)
    year = _Totals()
    refunds = 0
    cost_total = Decimal("0")
    buyers: set[str] = set()
    sold_by_week: dict[tuple[date, str], int] = defaultdict(int)  # (week start, sku) -> units
    product_weights = [p[6] for p in PRODUCTS]
    number = 1
    for day in _trading_days():
        factor = MONTH_FACTOR[day.month] * WEEKDAY_FACTOR[day.weekday()] * rng.uniform(0.85, 1.15)
        for _ in range(max(1, round(BASE_SALES_PER_DAY * factor))):
            name, sku, price, cost, rate, _category, _w = rng.choices(PRODUCTS, product_weights)[0]
            channel = rng.choices(CHANNELS, CHANNEL_WEIGHTS)[0]
            qty = rng.choice([1, 1, 2, 2, 3, 4]) if rate or float(price) < 10 else 1
            net = money(Decimal(price) * qty)
            vat = money(net * rate / 100)
            unit_cost = money(Decimal(cost) * qty)
            sign = -1 if rng.random() < 0.01 else 1
            refunds += sign < 0
            attach = 0.8 if channel == "Website" else 0.25
            email = ""
            if rng.random() < attach:
                email = rng.choices(emails, customer_weights)[0]
                buyers.add(email)
            sales_rows.append([
                uk(day), f"FB-{number:06d}", name, str(qty * sign), email, channel,
                pounds((net + vat) * sign), pounds(vat * sign), pounds(unit_cost),
            ])  # fmt: skip
            number += 1
            month_totals[day.strftime("%Y-%m")].add(net * sign, vat * sign)
            year.add(net * sign, vat * sign)
            cost_total += unit_cost  # the cost column is never negative, even on a refund
            sold_by_week[(day - timedelta(days=day.weekday()), sku)] += qty * sign

    # --- expenses (a plan per month; stock buying follows that month's sales) ------------------
    expense_rows: list[list[str]] = []
    expense_months: dict[str, _Totals] = defaultdict(_Totals)
    expense_year = _Totals()
    invoice = 1

    def spend(day: date, supplier: str, what: str, category: str, net: Decimal, rate: int) -> None:
        nonlocal invoice
        net = money(net)
        vat = money(net * rate / 100)
        expense_rows.append([uk(day), supplier, what, category, pounds(net), pounds(vat),
                             f"FB-INV-{invoice:04d}"])  # fmt: skip
        invoice += 1
        expense_months[day.strftime("%Y-%m")].add(net, vat)
        expense_year.add(net, vat)

    month = START.replace(day=1)
    while month <= END:
        key = month.strftime("%Y-%m")
        net_sales = month_totals[key].net
        spend(month.replace(day=1), "Demo Landlord Ltd", "Shop rent", "Rent", Decimal("1300"), 0)
        wages = Decimal("2900") + (Decimal("300") if month.month == 12 else Decimal("0"))
        spend(month.replace(day=28), "Demo Payroll Service", "Wages", "Wages", wages, 0)
        heating = Decimal("470") if month.month in (11, 12, 1, 2) else Decimal("390")
        spend(month.replace(day=12), "Demo Power Supplier", "Electricity and gas", "Utilities",
              heating + Decimal(rng.randint(0, 40)), 20)  # fmt: skip
        spend(month.replace(day=4), "Demo Flour Mill", "Flour and yeast", "Stock",
              net_sales * Decimal("0.17"), 0)  # fmt: skip
        spend(month.replace(day=6), "Demo Dairy Co", "Butter, eggs and milk", "Stock",
              net_sales * Decimal("0.11"), 20)  # fmt: skip
        spend(month.replace(day=15), "Demo Packaging", "Bags and boxes", "Packaging",
              Decimal(rng.randint(85, 115)), 20)  # fmt: skip
        spend(month.replace(day=10), "Demo Ad Platform", "Online adverts", "Marketing",
              Decimal("150"), 20)  # fmt: skip
        spend(month.replace(day=20), "Demo Accountants LLP", "Bookkeeping", "Professional fees",
              Decimal("180"), 20)  # fmt: skip
        if month.month in (1, 4, 7, 10):
            spend(month.replace(day=3), "Demo Insurance", "Shop insurance", "Insurance",
                  Decimal("420"), 0)  # fmt: skip
        if month.month in (2, 6, 9):
            spend(month.replace(day=18), "Demo Oven Repairs", "Oven repair", "Repairs",
                  Decimal(rng.randint(150, 600)), 20)  # fmt: skip
        month = (month + timedelta(days=32)).replace(day=1)

    # --- stock: opening count, then a delivery and a sales movement per product per week ----------
    stock_rows: list[list[str]] = []
    closing: dict[str, int] = {}
    reference = 1

    def move(day: date, name: str, sku: str, qty: int, kind: str, cost: str | None) -> None:
        nonlocal reference
        unit_cost = pounds(Decimal(cost)) if cost else ""
        stock_rows.append(
            [uk(day), name, sku, str(qty), kind, unit_cost, f"FB-MOV-{reference:05d}"]
        )
        reference += 1
        closing[sku] += qty

    for name, sku, _price, cost, *_rest in PRODUCTS:
        closing[sku] = 0
        move(START, name, sku, 200, "opening", cost)
    week = START - timedelta(days=START.weekday())
    while week <= END:
        for name, sku, _price, cost, *_rest in PRODUCTS:
            sold = sold_by_week.get((week, sku), 0)
            if sold:
                delivered = sold + max(1, round(sold * 0.05)) + rng.randint(0, 4)
                move(max(week, START), name, sku, delivered, "delivery", cost)
                move(min(week + timedelta(days=5), END), name, sku, -sold, "sale", None)
                if rng.random() < 0.4:
                    move(min(week + timedelta(days=4), END), name, sku, -rng.randint(1, 3),
                         "write_off", None)  # fmt: skip
        week += timedelta(days=7)
    low = {sku: n for sku, n in closing.items() if n < 0}
    if low:  # keep the invariant the data is meant to show: stock never goes negative
        raise AssertionError(f"Stock would end below zero: {low}")

    expected = {
        "business": BUSINESS_NAME,
        "period": {"from": START.isoformat(), "to": END.isoformat()},
        "counts": {
            "customers": len(customers),
            "products": len(products),
            "sales": len(sales_rows),
            "expenses": len(expense_rows),
            "stock_movements": len(stock_rows),
        },
        "sales": {
            **year.out(),
            "refund_rows": refunds,
            "cost_of_goods": str(cost_total),
            "customers_who_bought": len(buyers),
            "months": {k: v.out() for k, v in sorted(month_totals.items())},
        },  # fmt: skip
        "expenses": {
            **expense_year.out(),
            "months": {k: v.out() for k, v in sorted(expense_months.items())},
        },  # fmt: skip
        "stock": {"closing": closing},
    }
    files = {
        "milestone-customers.csv": _csv(CUSTOMER_HEADER, customers),
        "milestone-products.csv": _csv(PRODUCT_HEADER, products),
        "milestone-sales.csv": _csv(SALES_HEADER, sales_rows),
        "milestone-expenses.csv": _csv(EXPENSE_HEADER, expense_rows),
        "milestone-stock.csv": _csv(STOCK_HEADER, stock_rows),
    }
    return Dataset(files=files, expected=expected)
