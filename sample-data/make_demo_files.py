"""Makes the FAKE demo files for trying Vyterlix. Nothing here is real: the shop, the
customers, the products and every figure are invented, and the emails use example.com.

    python make_demo_files.py        # writes the .csv files next to this script

The same seed gives the same files every time. They are built to show things off:
- demo-sales-2026.csv has no sales at all in March 2026 (a gap the data-quality screen finds)
  and about 3 in 10 rows have no cost of goods;
- demo-expenses-2026.csv only starts in June, and some expenses have no category;
- demo-sales-october-with-problems.csv has deliberate mistakes (an impossible date, a US-style
  date, a comma used for pence, a blank amount) and two repeated rows, to try the problem report.
"""

import csv
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

OUT = Path(__file__).parent
rng = random.Random(2026)
PENNY = Decimal("0.01")

# name, sku, price ex VAT, cost, VAT rate %, category
PRODUCTS = [
    ("Demo Sourdough Loaf", "DEMO-001", "4.20", "1.10", 0, "Bread"),
    ("Demo Seeded Batch", "DEMO-002", "3.60", "0.95", 0, "Bread"),
    ("Demo Baguette", "DEMO-003", "2.10", "0.55", 0, "Bread"),
    ("Demo Croissant", "DEMO-004", "1.90", "0.48", 0, "Pastry"),
    ("Demo Pain au Chocolat", "DEMO-005", "2.20", "0.60", 0, "Pastry"),
    ("Demo Cinnamon Swirl", "DEMO-006", "2.60", "0.70", 20, "Pastry"),
    ("Demo Victoria Sponge Slice", "DEMO-007", "3.10", "0.85", 20, "Cakes"),
    ("Demo Lemon Drizzle Slice", "DEMO-008", "3.10", "0.80", 20, "Cakes"),
    ("Demo Birthday Cake", "DEMO-009", "28.00", "7.50", 20, "Cakes"),
    ("Demo Flat White", "DEMO-010", "2.90", "0.45", 20, "Drinks"),
    ("Demo Filter Coffee", "DEMO-011", "2.20", "0.30", 20, "Drinks"),
    ("Demo Sausage Roll (hot)", "DEMO-012", "2.50", "0.90", 20, "Hot food"),
]
CHANNELS = ["Shop", "Shop", "Shop", "Website", "Market stall"]
FAKE_POSTCODES = ["ZZ1 1AA", "ZZ1 2BB", "ZZ2 3CC", "ZZ3 4DD", "ZZ4 5EE", "ZZ5 6FF"]


def money(value) -> Decimal:
    return Decimal(value).quantize(PENNY, rounding=ROUND_HALF_UP)


def pounds(value: Decimal) -> str:
    return f"£{value:,.2f}"


def uk(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def write(name: str, header: list[str], rows: list[list[str]]) -> None:
    # UTF-8 with a byte-order mark, so Excel in the UK shows £ correctly.
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"{name}: {len(rows)} rows")


def sale_row(number: int, day: date, *, with_customer: bool) -> list[str]:
    name, _, price, cost, rate, _ = rng.choice(PRODUCTS)
    qty = rng.choice([1, 1, 1, 2, 2, 3]) if rate or float(price) < 10 else 1
    net = money(Decimal(price) * qty)
    vat = money(net * rate / 100)
    refund = rng.random() < 0.01
    sign = -1 if refund else 1
    cost_total = money(Decimal(cost) * qty) if rng.random() < 0.7 else None
    email = f"demo{rng.randint(1, 40):03d}@example.com" if with_customer and rng.random() < 0.45 else ""
    return [
        uk(day),
        f"DEMO-{number:06d}",
        name,
        str(qty * sign),
        email,
        rng.choice(CHANNELS),
        pounds((net + vat) * sign),
        pounds(vat * sign),
        pounds(cost_total) if cost_total is not None else "",
    ]


SALES_HEADER = ["Date", "Receipt No", "Item", "Qty", "Customer Email", "Channel", "Total (inc VAT)", "VAT", "Cost"]


def sales_file() -> None:
    rows, number, day = [], 1, date(2026, 1, 1)
    while day <= date(2026, 9, 28):
        if day.weekday() != 6 and day.month != 3:  # shut on Sundays; nothing recorded in March
            for _ in range(rng.randint(6, 14)):
                rows.append(sale_row(number, day, with_customer=True))
                number += 1
        day += timedelta(days=1)
    write("demo-sales-2026.csv", SALES_HEADER, rows)


def problem_sales_file() -> None:
    rows, number = [], 900_001
    for offset in range(14):
        day = date(2026, 9, 29) + timedelta(days=offset // 3)
        rows.append(sale_row(number, min(day, date(2026, 10, 1)), with_customer=False))
        number += 1
    good = rows[:]
    rows[3][0] = "31/04/2026"  # not a real date
    rows[5][0] = "09/28/2026"  # US-style: there is no month 28
    rows[7][6] = "£4,50"  # a comma used for pence
    rows[9][6] = ""  # blank amount
    rows.append(good[0][:])  # two exact repeats
    rows.append(good[1][:])
    write("demo-sales-october-with-problems.csv", SALES_HEADER, rows)


def expenses_file() -> None:
    rows, invoice = [], 1
    plan = [
        ("Demo Landlord Ltd", "Shop rent", "Rent", "1400.00", 0),
        ("Demo Flour Mill", "Flour and yeast", "Stock", "620.00", 20),
        ("Demo Dairy Co", "Butter and milk", "Stock", "340.00", 20),
        ("Demo Power Supplier", "Electricity and gas", "Utilities", "410.00", 20),
        ("Demo Packaging", "Bags and boxes", "", "96.00", 20),  # no category
        ("Demo Payroll Service", "Wages", "Wages", "2850.00", 0),
    ]
    for month in range(6, 10):
        for supplier, what, category, net, rate in plan:
            net_d = Decimal(net) * Decimal(str(rng.uniform(0.92, 1.08)))
            net_d = money(net_d if what != "Shop rent" and what != "Wages" else Decimal(net))
            vat = money(net_d * rate / 100)
            rows.append([uk(date(2026, month, rng.randint(2, 27))), supplier, what, category, pounds(net_d), pounds(vat), f"DEMO-INV-{invoice:04d}"])
            invoice += 1
    write("demo-expenses-2026.csv", ["Date", "Supplier", "Description", "Category", "Net", "VAT", "Invoice No"], rows)


def products_file() -> None:
    rows = [[n, sku, pounds(Decimal(price)), pounds(Decimal(cost)), f"{rate}%", cat] for n, sku, price, cost, rate, cat in PRODUCTS]
    write("demo-products.csv", ["Name", "SKU", "Price", "Cost", "VAT rate", "Category"], rows)


def customers_file() -> None:
    rows = []
    for n in range(1, 41):
        rows.append([f"Demo Customer {n:03d}", f"demo{n:03d}@example.com", rng.choice(FAKE_POSTCODES), rng.choice(["Retail", "Retail", "Trade"])])
    write("demo-customers.csv", ["Name", "Email", "Postcode", "Type"], rows)


if __name__ == "__main__":
    sales_file()
    problem_sales_file()
    expenses_file()
    products_file()
    customers_file()
