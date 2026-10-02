"""Building the business's records from checked rows (Phase 4 step 6).

Rows arrive already judged by `parse_row`. Here each one becomes real data:

- sales -> a sale, plus one line when the row says what was sold (product, quantity or cost)
- expenses -> an expense
- customers, suppliers, products, stock movements -> themselves

Sales and expenses also point at customers, products, suppliers, sales channels and cost
categories. Those are found by name (or email / product code) and created only when they
don't exist yet. Everything created gets the import's id, so undoing the import removes it
(sales channels and cost categories are the business's own lists, not data, and stay).

Work is done in batches: one lookup per kind per batch, one insert per table per batch.
"""

import uuid
from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, StockMovement, Supplier
from app.services.import_rows import RowOutcome

MODELS = {
    "sales": Sale,
    "sale_lines": SaleLine,
    "expenses": Expense,
    "stock_movements": StockMovement,
    "customers": Customer,
    "suppliers": Supplier,
    "products": Product,
}
# Where each kind of file's own records go (the table a row points at).
MAIN_TABLE = {
    "sales": "sales",
    "expenses": "expenses",
    "stock_movements": "stock_movements",
    "customers": "customers",
    "suppliers": "suppliers",
    "products": "products",
}
# Insert order (parents before children) and the reverse for undo.
INSERT_ORDER = (
    "sales",
    "sale_lines",
    "expenses",
    "stock_movements",
    "customers",
    "suppliers",
    "products",
)
UNIT_PRICE_PLACES = Decimal("0.0001")


def _customer_key(values: dict[str, Any]) -> tuple[str, str] | None:
    if values.get("customer_email"):
        return ("email", values["customer_email"])
    if values.get("customer_name"):
        return ("name", values["customer_name"].casefold())
    return None


def _product_key(sku: str | None, name: str | None) -> tuple[str, str] | None:
    if sku:
        return ("sku", sku)
    if name:
        return ("name", name.casefold())
    return None


def _first_spellings(names) -> dict[str, str]:
    """{lower-case: name as first written}: "Market stall" then "market STALL" is one entry."""
    wanted: dict[str, str] = {}
    for name in names:
        wanted.setdefault(name.casefold(), name)
    return wanted


class Resolver:
    """Finds, or creates, the things rows refer to. Caches for the whole import."""

    def __init__(self, db: Session, organization_id: uuid.UUID, import_id: uuid.UUID, source: str):
        self.db = db
        self.common = {"organization_id": organization_id, "source": source, "import_id": import_id}
        self.organization_id = organization_id
        self.customers: dict[tuple[str, str], uuid.UUID] = {}
        self.products: dict[tuple[str, str], uuid.UUID] = {}
        self.suppliers: dict[str, uuid.UUID] = {}
        self.items: dict[tuple[str, str], uuid.UUID] = {}
        self.created: Counter[str] = Counter()

    # --- what a batch of rows needs ---------------------------------------------------------------

    def prepare(self, dataset: str, outcomes: list[RowOutcome]) -> None:
        values = [o.values for o in outcomes]
        if dataset == "sales":
            self._customers(values)
            self._products([(v.get("sku"), v.get("product")) for v in values])
            self._items("sales_channel", [v.get("channel") for v in values])
        elif dataset == "expenses":
            self._suppliers([v.get("supplier") for v in values])
            self._items("cost_category", [v.get("category") for v in values])
        elif dataset == "customers":
            self._items("customer_type", [v.get("customer_type") for v in values])
        elif dataset == "products":
            self._items("offering", [v.get("category") for v in values])
        elif dataset == "stock_movements":
            self._products([(v.get("sku"), v.get("product")) for v in values])

    # --- lookups used while building --------------------------------------------------------------

    def customer_id(self, values: dict[str, Any]) -> uuid.UUID | None:
        key = _customer_key(values)
        return self.customers.get(key) if key else None

    def product_id(self, sku: str | None, name: str | None) -> uuid.UUID | None:
        key = _product_key(sku, name)
        return self.products.get(key) if key else None

    def supplier_id(self, name: str | None) -> uuid.UUID | None:
        return self.suppliers.get(name.casefold()) if name else None

    def item_id(self, kind: str, name: str | None) -> uuid.UUID | None:
        return self.items.get((kind, name.casefold())) if name else None

    # --- find or create ----------

    def _insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.db.execute(insert(MODELS[table]), rows)
            self.created[table] += len(rows)

    def _customers(self, batch: list[dict[str, Any]]) -> None:
        wanted: dict[tuple[str, str], dict[str, Any]] = {}
        for v in batch:
            key = _customer_key(v)
            if key and key not in self.customers:
                wanted.setdefault(
                    key,
                    {
                        "name": v.get("customer_name"),
                        "email": v.get("customer_email"),
                        "postcode": v.get("customer_postcode"),
                    },
                )
        if not wanted:
            return
        emails = [k[1] for k in wanted if k[0] == "email"]
        names = [k[1] for k in wanted if k[0] == "name"]
        if emails:
            rows = self.db.execute(
                select(Customer.id, Customer.email).where(Customer.email.in_(emails))
            )
            for found_id, email in rows:
                self.customers.setdefault(("email", email), found_id)
        if names:
            lowered = func.lower(Customer.name)
            rows = self.db.execute(
                select(Customer.id, lowered).where(lowered.in_(names)).order_by(Customer.created_at)
            )
            for found_id, name in rows:
                self.customers.setdefault(("name", name), found_id)
        new = []
        for key, data in wanted.items():
            if key not in self.customers:
                self.customers[key] = uuid.uuid4()
                new.append({"id": self.customers[key], **data, **self.common})
        self._insert("customers", new)

    def _products(self, batch: list[tuple[str | None, str | None]]) -> None:
        wanted: dict[tuple[str, str], dict[str, Any]] = {}
        for sku, name in batch:
            key = _product_key(sku, name)
            if key and key not in self.products:
                wanted.setdefault(key, {"name": name or sku, "sku": sku})
        if not wanted:
            return
        skus = [k[1] for k in wanted if k[0] == "sku"]
        names = [k[1] for k in wanted if k[0] == "name"]
        if skus:
            for found_id, sku in self.db.execute(
                select(Product.id, Product.sku).where(Product.sku.in_(skus))
            ):
                self.products.setdefault(("sku", sku), found_id)
        if names:
            lowered = func.lower(Product.name)
            rows = self.db.execute(
                select(Product.id, lowered).where(lowered.in_(names)).order_by(Product.created_at)
            )
            for found_id, name in rows:
                self.products.setdefault(("name", name), found_id)
        new = []
        for key, data in wanted.items():
            if key not in self.products:
                self.products[key] = uuid.uuid4()
                new.append({"id": self.products[key], **data, **self.common})
        self._insert("products", new)

    def _suppliers(self, names: list[str | None]) -> None:
        wanted = _first_spellings(n for n in names if n and n.casefold() not in self.suppliers)
        if not wanted:
            return
        lowered = func.lower(Supplier.name)
        rows = self.db.execute(
            select(Supplier.id, lowered)
            .where(lowered.in_(list(wanted)))
            .order_by(Supplier.created_at)
        )
        for found_id, name in rows:
            self.suppliers.setdefault(name, found_id)
        new = []
        for key, name in wanted.items():
            if key not in self.suppliers:
                self.suppliers[key] = uuid.uuid4()
                new.append({"id": self.suppliers[key], "name": name, **self.common})
        self._insert("suppliers", new)

    def _items(self, kind: str, names: list[str | None]) -> None:
        wanted = _first_spellings(n for n in names if n and (kind, n.casefold()) not in self.items)
        if not wanted:
            return
        lowered = func.lower(BusinessListItem.name)
        rows = self.db.execute(
            select(BusinessListItem.id, lowered).where(
                BusinessListItem.kind == kind, lowered.in_(list(wanted))
            )
        )
        for found_id, name in rows:
            self.items[(kind, name)] = found_id
        new = []
        for key, name in wanted.items():
            if (kind, key) not in self.items:
                self.items[(kind, key)] = uuid.uuid4()
                new.append(
                    {
                        "id": self.items[(kind, key)],
                        "organization_id": self.organization_id,
                        "kind": kind,
                        "name": name,
                    }
                )
        if new:
            self.db.execute(insert(BusinessListItem), new)
            self.created["business_list_items"] += len(new)


# --- one row -> records ------------------------------------------------


def build(
    dataset: str, resolver: Resolver, outcomes: list[RowOutcome]
) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, uuid.UUID]]]:
    """(rows to insert per table, and for each outcome the (table, id) of its own record)."""
    resolver.prepare(dataset, outcomes)
    tables: dict[str, list[dict[str, Any]]] = {t: [] for t in INSERT_ORDER}
    targets: list[tuple[str, uuid.UUID]] = []
    for outcome in outcomes:
        record_id = uuid.uuid4()
        _BUILDERS[dataset](resolver, outcome.values, record_id, tables)
        targets.append((MAIN_TABLE[dataset], record_id))
    return tables, targets


def _sale(r: Resolver, v: dict[str, Any], sale_id: uuid.UUID, tables: dict) -> None:
    tables["sales"].append(
        {
            "id": sale_id,
            "kind": v["kind"],
            "sold_on": v["sold_on"],
            "customer_id": r.customer_id(v),
            "sales_channel_id": r.item_id("sales_channel", v.get("channel")),
            "net_amount": v["net_amount"],
            "vat_amount": v["vat_amount"],
            "gross_amount": v["gross_amount"],
            "discount_amount": v.get("discount") or Decimal(0),
            "notes": v.get("notes"),
            "source_ref": v.get("reference"),
            **r.common,
        }
    )
    if not any(v.get(k) is not None for k in ("product", "sku", "quantity", "cost")):
        return
    quantity = abs(v["quantity"]) if v.get("quantity") is not None else Decimal(1)
    if v["kind"] == "refund":
        quantity = -quantity
    tables["sale_lines"].append(
        {
            "id": uuid.uuid4(),
            "organization_id": r.organization_id,
            "sale_id": sale_id,
            "import_id": r.common["import_id"],
            "product_id": r.product_id(v.get("sku"), v.get("product")),
            "description": v.get("product") or v.get("sku"),
            "quantity": quantity,
            "unit_price_ex_vat": (abs(v["net_amount"]) / abs(quantity)).quantize(UNIT_PRICE_PLACES),
            "net_amount": v["net_amount"],
            "vat_amount": v["vat_amount"],
            "cost_amount": v.get("cost"),
        }
    )


def _expense(r: Resolver, v: dict[str, Any], expense_id: uuid.UUID, tables: dict) -> None:
    tables["expenses"].append(
        {
            "id": expense_id,
            "kind": v["kind"],
            "spent_on": v["spent_on"],
            "supplier_id": r.supplier_id(v.get("supplier")),
            "cost_category_id": r.item_id("cost_category", v.get("category")),
            "description": v.get("description"),
            "net_amount": v["net_amount"],
            "vat_amount": v["vat_amount"],
            "gross_amount": v["gross_amount"],
            "source_ref": v.get("reference"),
            **r.common,
        }
    )


def _customer(r: Resolver, v: dict[str, Any], customer_id: uuid.UUID, tables: dict) -> None:
    tables["customers"].append(
        {
            "id": customer_id,
            "name": v.get("name"),
            "email": v.get("email"),
            "postcode": v.get("postcode"),
            "customer_type_id": r.item_id("customer_type", v.get("customer_type")),
            "source_ref": v.get("reference"),
            **r.common,
        }
    )


def _supplier(r: Resolver, v: dict[str, Any], supplier_id: uuid.UUID, tables: dict) -> None:
    tables["suppliers"].append(
        {"id": supplier_id, "name": v["name"], "source_ref": v.get("reference"), **r.common}
    )


def _product(r: Resolver, v: dict[str, Any], product_id: uuid.UUID, tables: dict) -> None:
    tables["products"].append(
        {
            "id": product_id,
            "name": v["name"],
            "sku": v.get("sku"),
            "offering_id": r.item_id("offering", v.get("category")),
            "unit_price_ex_vat": v.get("unit_price_ex_vat"),
            "unit_cost": v.get("unit_cost"),
            "vat_rate": v.get("vat_rate"),
            **r.common,
        }
    )


def _stock_movement(r: Resolver, v: dict[str, Any], movement_id: uuid.UUID, tables: dict) -> None:
    tables["stock_movements"].append(
        {
            "id": movement_id,
            "product_id": r.product_id(v.get("sku"), v.get("product")),
            "moved_on": v["moved_on"],
            "kind": v["kind"],
            "quantity": v["quantity"],
            "unit_cost": v.get("unit_cost"),
            "notes": v.get("notes"),
            "source_ref": v.get("reference"),
            **r.common,
        }
    )


_BUILDERS = {
    "sales": _sale,
    "expenses": _expense,
    "customers": _customer,
    "suppliers": _supplier,
    "products": _product,
    "stock_movements": _stock_movement,
}


def insert_records(db: Session, tables: dict[str, list[dict[str, Any]]], created: Counter) -> None:
    for table in INSERT_ORDER:
        if tables[table]:
            db.execute(insert(MODELS[table]), tables[table])
            created[table] += len(tables[table])
