"""Reading one row of an upload into proper values, or saying exactly what is wrong with it.

`parse_row` is pure (no database): the same function judges rows during validation (step 5)
and builds the records during the import (step 6), so what the person was shown is what
gets imported. Only the columns the person mapped are looked at.

Money rules (decision 2026-09-28): revenue and costs are stored excluding VAT with the VAT
alongside, gross = net + VAT. The person says whether the file's amounts include VAT; the VAT
comes from a VAT column, else a rate column, else the rate they chose for the whole file.
Negative amounts are refunds (sales) or credits (expenses).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.data import STOCK_MOVEMENT_KINDS
from app.services import import_fields as fields
from app.services import value_parsers as vp
from app.services.value_parsers import CellError

_TOTALS_DATASETS = ("sales", "expenses")
_QUANTITY_DIRECTION = {
    "opening": "zero or more",
    "delivery": "more than zero",
    "return": "more than zero",
    "sale": "less than zero",
    "write_off": "less than zero",
    "adjustment": "not zero",
}


@dataclass
class RowOutcome:
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    reference: str | None = None
    key: tuple[str, str] | None = None  # how repeats of this row are recognised

    @property
    def ok(self) -> bool:
        return not self.errors


class _Row:
    """The mapped cells of one row, plus the problems found while reading them."""

    def __init__(self, dataset: str, raw: dict[str, str], mapping: dict[str, str]) -> None:
        self.raw, self.mapping = raw, mapping
        self.labels = {f.key: f.label for f in fields.DATASET_FIELDS[dataset]}
        self.out = RowOutcome()

    def mapped(self, key: str) -> bool:
        return key in self.mapping

    def cell(self, key: str) -> str:
        header = self.mapping.get(key)
        return self.raw.get(header, "").strip() if header else ""

    def error(self, key: str, code: str, message: str) -> None:
        self.out.errors.append({"field": key, "code": code, "message": message})

    def read(self, key: str, parser: Callable[[str], Any], *, required: bool = False) -> Any:
        """Parse a cell. Blank -> None (an error if required). A bad cell records the error."""
        text = self.cell(key)
        if not text:
            if required:
                self.error(key, "missing_value", f"{self.labels[key]} is blank.")
            return None
        try:
            return parser(text)
        except CellError as exc:
            self.error(key, exc.code, exc.message)
            return None

    def text(self, key: str, max_length: int, *, required: bool = False) -> str | None:
        label = self.labels[key].lower()
        return self.read(
            key, lambda t: vp.clean_text(t, max_length=max_length, what=label), required=required
        )


def parse_row(
    dataset: str,
    raw: dict[str, str],
    mapping: dict[str, str],
    options: dict[str, Any],
    *,
    today: date | None = None,
) -> RowOutcome:
    row = _Row(dataset, raw, mapping)
    _PARSERS[dataset](row, options, today)
    return row.out


# --- sales and expenses ------------------------------------------------------------------------


def _split_vat(
    row: _Row, amount: Decimal, options: dict[str, Any]
) -> tuple[Decimal, Decimal, Decimal] | None:
    """(net, vat, gross) with the sign of the amount, or None if VAT can't be worked out."""
    sign = -1 if amount < 0 else 1
    shown = abs(amount)
    inclusive = bool(options.get("vat_inclusive"))

    vat_cell = row.cell("vat_amount")
    if row.mapped("vat_amount") and vat_cell:
        vat = row.read("vat_amount", vp.parse_money)
        if vat is None:
            return None
        vat = abs(vat)
        net = shown - vat if inclusive else shown
        gross = shown if inclusive else shown + vat
        if net < 0:
            row.error("vat_amount", "vat_exceeds_amount", "The VAT is more than the amount.")
            return None
    else:
        rate = None
        if row.mapped("vat_rate") and row.cell("vat_rate"):
            rate = row.read("vat_rate", vp.parse_vat_rate)
            if rate is None:
                return None
        elif options.get("default_vat_rate") is not None:
            rate = Decimal(options["default_vat_rate"])
        else:
            row.error(
                "vat_amount",
                "vat_missing",
                "There is no VAT on this row and no VAT rate was chosen for the file.",
            )
            return None
        if inclusive:
            net = vp.round_pennies(shown / (1 + rate / 100))
            vat, gross = shown - net, shown
        else:
            vat = vp.round_pennies(shown * rate / 100)
            net, gross = shown, shown + vat
    return sign * net, sign * vat, sign * gross


def _transaction(row: _Row, options: dict, today: date | None, *, dataset: str) -> None:
    date_key = "sold_on" if dataset == "sales" else "spent_on"
    out = row.out
    when = row.read(date_key, lambda t: vp.parse_date(t, today=today), required=True)
    amount = row.read("amount", vp.parse_money, required=True)
    split = None
    if amount is not None:
        if amount == 0:
            row.error("amount", "zero_amount", "The amount is zero, so there is nothing to record.")
        else:
            split = _split_vat(row, amount, options)
    reference = row.text("reference", 200)

    if dataset == "sales":
        positive, negative = "sale", "refund"
        discount = row.read("discount", vp.parse_money)
        cost = row.read("cost", vp.parse_money)
        quantity = row.read("quantity", vp.parse_quantity)
        if quantity is not None and quantity == 0:
            row.error("quantity", "zero_quantity", "The quantity is zero.")
        email = row.read("customer_email", vp.parse_email)
        postcode = row.read("customer_postcode", vp.parse_postcode)
        extras = {
            "discount": abs(discount) if discount is not None else None,
            "cost": abs(cost) if cost is not None else None,
            "quantity": quantity,
            "customer_name": row.text("customer_name", 200),
            "customer_email": email,
            "customer_postcode": postcode,
            "product": row.text("product", 200),
            "sku": row.text("sku", 100),
            "channel": row.text("channel", 100),
            "notes": row.text("notes", 2000),
        }
    else:
        positive, negative = "expense", "credit"
        extras = {
            "supplier": row.text("supplier", 200),
            "category": row.text("category", 100),
            "description": row.text("description", 300),
        }

    if when is not None and split is not None and amount is not None:
        net, vat, gross = split
        out.values.update(
            {
                "kind": negative if amount < 0 else positive,
                date_key: when,
                "net_amount": net,
                "vat_amount": vat,
                "gross_amount": gross,
                "reference": reference,
                **extras,
            }
        )
    out.reference = reference
    out.key = ("ref", reference) if reference else None


# --- customers, suppliers, products, stock ------------------------------------------------------


def _customer(row: _Row, options: dict, today: date | None) -> None:
    name = row.text("name", 200)
    email = row.read("email", vp.parse_email)
    postcode = row.read("postcode", vp.parse_postcode)
    kind = row.text("customer_type", 100)
    reference = row.text("reference", 200)
    if not name and not email and not row.out.errors:
        row.error("name", "empty_row", "This row has neither a name nor an email address.")
    row.out.values.update(
        {
            "name": name,
            "email": email,
            "postcode": postcode,
            "customer_type": kind,
            "reference": reference,
        }
    )
    row.out.reference = reference
    row.out.key = ("ref", reference) if reference else ("email", email) if email else None


def _supplier(row: _Row, options: dict, today: date | None) -> None:
    name = row.text("name", 200, required=True)
    reference = row.text("reference", 200)
    row.out.values.update({"name": name, "reference": reference})
    row.out.reference = reference
    row.out.key = ("name", name.casefold()) if name else None


def _price_ex_vat(row: _Row, price: Decimal, rate: Decimal | None, options: dict) -> Decimal | None:
    if price < 0:
        row.error("unit_price", "negative_amount", "The price can't be negative.")
        return None
    if not options.get("vat_inclusive"):
        return price
    if rate is None:
        rate_text = options.get("default_vat_rate")
        if rate_text is None:
            row.error("vat_rate", "vat_missing", "There is no VAT rate for this price.")
            return None
        rate = Decimal(rate_text)
    return (price / (1 + rate / 100)).quantize(Decimal("0.0001"))


def _product(row: _Row, options: dict, today: date | None) -> None:
    name = row.text("name", 200, required=True)
    sku = row.text("sku", 100)
    category = row.text("category", 100)
    rate = row.read("vat_rate", vp.parse_vat_rate)
    price = row.read("unit_price", vp.parse_money)
    cost = row.read("unit_cost", vp.parse_money)
    price_ex = _price_ex_vat(row, price, rate, options) if price is not None else None
    if cost is not None and cost < 0:
        row.error("unit_cost", "negative_amount", "The cost can't be negative.")
    row.out.values.update(
        {
            "name": name,
            "sku": sku,
            "category": category,
            "vat_rate": rate if rate is not None else _default_rate(options),
            "unit_price_ex_vat": price_ex,
            "unit_cost": cost,
        }
    )
    row.out.reference = sku
    row.out.key = ("sku", sku) if sku else ("name", name.casefold()) if name else None


def _default_rate(options: dict) -> Decimal | None:
    rate = options.get("default_vat_rate")
    return Decimal(rate) if rate is not None else None


def _movement_kind(text: str) -> str:
    kind = text.strip().lower().replace("-", "_").replace(" ", "_")
    kind = {"writeoff": "write_off", "written_off": "write_off", "sold": "sale"}.get(kind, kind)
    if kind not in STOCK_MOVEMENT_KINDS:
        raise CellError(
            "invalid_movement_kind",
            f"'{text.strip()}' isn't a type of stock movement. Use one of: "
            + ", ".join(STOCK_MOVEMENT_KINDS)
            + ".",
        )
    return kind


def _stock_movement(row: _Row, options: dict, today: date | None) -> None:
    moved_on = row.read("moved_on", lambda t: vp.parse_date(t, today=today), required=True)
    product = row.text("product", 200)
    sku = row.text("sku", 100)
    if (
        not product
        and not sku
        and not any(e["field"] in ("product", "sku") for e in row.out.errors)
    ):
        row.error("product", "missing_value", "Neither the product nor its code is filled in.")
    quantity = row.read("quantity", vp.parse_quantity, required=True)
    unit_cost = row.read("unit_cost", vp.parse_money)
    kind = row.read("movement_kind", _movement_kind)
    if quantity is not None:
        if kind is None and not row.cell("movement_kind"):
            kind = "delivery" if quantity > 0 else "adjustment" if quantity < 0 else None
        if quantity == 0 and kind != "opening":  # opening stock of none is a real fact
            row.error("quantity", "zero_quantity", "The quantity is zero.")
        elif kind is not None and not _direction_ok(kind, quantity):
            row.error(
                "quantity",
                "sign_mismatch",
                f"A '{kind}' movement needs a quantity that is {_QUANTITY_DIRECTION[kind]}.",
            )
    reference = row.text("reference", 200)
    row.out.values.update(
        {
            "moved_on": moved_on,
            "product": product,
            "sku": sku,
            "quantity": quantity,
            "kind": kind,
            "unit_cost": unit_cost,
            "notes": row.text("notes", 300),
            "reference": reference,
        }
    )
    row.out.reference = reference
    row.out.key = ("ref", reference) if reference else None


def _direction_ok(kind: str, quantity: Decimal) -> bool:
    return {
        "opening": quantity >= 0,
        "delivery": quantity > 0,
        "return": quantity > 0,
        "sale": quantity < 0,
        "write_off": quantity < 0,
        "adjustment": quantity != 0,
    }[kind]


_PARSERS: dict[str, Callable[[_Row, dict, date | None], None]] = {
    "sales": lambda r, o, t: _transaction(r, o, t, dataset="sales"),
    "expenses": lambda r, o, t: _transaction(r, o, t, dataset="expenses"),
    "customers": _customer,
    "suppliers": _supplier,
    "products": _product,
    "stock_movements": _stock_movement,
}
