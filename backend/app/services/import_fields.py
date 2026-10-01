"""What an uploaded file can fill, and how its columns are matched to those fields.

Each dataset (sales, expenses, ...) has a list of fields. A mapping says which column
of the file feeds which field: {"sold_on": "Date", "amount": "Total (inc VAT)"}.

Matching is deliberately conservative: a column is suggested only when its heading is
(after ignoring case, spaces and punctuation) one of a field's known names. A wrong
automatic guess would quietly put the wrong numbers into someone's accounts, so
anything unsure is left for the person to choose.

Money: each import is asked once whether the amounts include VAT (decision 2026-09-28).
If the file has no VAT column, the VAT rate must be given too, because splitting a
total into net + VAT needs it. Revenue is stored excluding VAT (ADR 0001 section 4).
"""

import re
from dataclasses import dataclass

from app.models.data import UK_VAT_RATES


@dataclass(frozen=True)
class ImportField:
    key: str
    label: str
    help: str
    type: str  # date | money | quantity | rate | text | email | postcode | choice
    aliases: tuple[str, ...] = ()


def _f(key, label, help, type, *aliases) -> ImportField:
    return ImportField(key, label, help, type, aliases)


_REFERENCE_HELP = "A number that identifies this row in your own system. Used to spot repeats."

SALES = (
    _f(
        "sold_on",
        "Date of sale",
        "Day the sale happened (UK format, e.g. 28/09/2026).",
        "date",
        "date",
        "sale date",
        "sold on",
        "order date",
        "transaction date",
        "invoice date",
        "date of sale",
        "day",
        "created at",
        "timestamp",
    ),
    _f(
        "amount",
        "Sale amount",
        "What was charged. Negative amounts are treated as refunds.",
        "money",
        "total",
        "amount",
        "sale amount",
        "sales",
        "revenue",
        "gross",
        "net",
        "total inc vat",
        "total ex vat",
        "total including vat",
        "total excluding vat",
        "gross amount",
        "net amount",
        "value",
        "sale total",
        "order total",
    ),
    _f(
        "vat_amount",
        "VAT amount",
        "The VAT charged, if the file has it.",
        "money",
        "vat",
        "vat amount",
        "tax",
        "tax amount",
        "vat charged",
    ),
    _f(
        "vat_rate",
        "VAT rate",
        "The VAT rate for the row (20, 5 or 0).",
        "rate",
        "vat rate",
        "tax rate",
        "vat %",
        "vat percent",
    ),
    _f(
        "reference",
        "Order or receipt number",
        _REFERENCE_HELP,
        "text",
        "order id",
        "order number",
        "order no",
        "order ref",
        "invoice",
        "invoice number",
        "invoice no",
        "reference",
        "ref",
        "receipt",
        "receipt number",
        "receipt no",
        "receipt id",
        "transaction id",
        "transaction no",
        "transaction number",
        "sale id",
        "sale number",
        "ref no",
        "invoice id",
        "id",
    ),
    _f(
        "customer_name",
        "Customer name",
        "Only map this if you need it.",
        "text",
        "customer",
        "customer name",
        "name",
        "client",
        "client name",
    ),
    _f(
        "customer_email",
        "Customer email",
        "Only map this if you need it.",
        "email",
        "email",
        "customer email",
        "email address",
    ),
    _f(
        "customer_postcode",
        "Customer postcode",
        "UK postcode, e.g. LS1 4AP.",
        "postcode",
        "postcode",
        "post code",
        "customer postcode",
        "delivery postcode",
        "zip",
    ),
    _f(
        "product",
        "Product or service",
        "What was sold.",
        "text",
        "product",
        "product name",
        "item",
        "item name",
        "description",
        "service",
    ),
    _f(
        "sku",
        "Product code (SKU)",
        "Your stock code for the product.",
        "text",
        "sku",
        "product code",
        "item code",
        "stock code",
        "barcode",
    ),
    _f(
        "quantity",
        "Quantity",
        "How many were sold.",
        "quantity",
        "quantity",
        "qty",
        "units",
        "number sold",
    ),
    _f(
        "cost",
        "Cost of goods",
        "What the goods cost you (excluding VAT). Leave unmapped if unknown.",
        "money",
        "cost",
        "cost of goods",
        "cogs",
        "cost price",
        "unit cost",
    ),
    _f(
        "discount",
        "Discount",
        "Money taken off the sale.",
        "money",
        "discount",
        "discount amount",
        "reduction",
    ),
    _f(
        "channel",
        "Sales channel",
        "Where the sale happened (shop, website, market...).",
        "text",
        "channel",
        "sales channel",
        "source",
        "outlet",
        "store",
        "location",
    ),
    _f("notes", "Notes", "Anything else worth keeping.", "text", "notes", "note", "comment"),
)

EXPENSES = (
    _f(
        "spent_on",
        "Date of expense",
        "Day the money was spent (UK format).",
        "date",
        "date",
        "expense date",
        "spent on",
        "invoice date",
        "transaction date",
        "payment date",
    ),
    _f(
        "amount",
        "Expense amount",
        "What was paid. Negative amounts are treated as credits.",
        "money",
        "total",
        "amount",
        "expense",
        "expenses",
        "cost",
        "gross",
        "net",
        "total inc vat",
        "total ex vat",
        "gross amount",
        "net amount",
        "value",
        "paid",
    ),
    _f(
        "vat_amount",
        "VAT amount",
        "The VAT on the expense, if the file has it.",
        "money",
        "vat",
        "vat amount",
        "tax",
        "tax amount",
    ),
    _f(
        "vat_rate",
        "VAT rate",
        "The VAT rate for the row (20, 5 or 0).",
        "rate",
        "vat rate",
        "tax rate",
        "vat %",
    ),
    _f(
        "reference",
        "Invoice or reference number",
        _REFERENCE_HELP,
        "text",
        "invoice",
        "invoice number",
        "invoice no",
        "reference",
        "ref",
        "bill number",
        "bill no",
        "bill id",
        "invoice id",
        "ref no",
        "id",
    ),
    _f(
        "supplier",
        "Supplier",
        "Who you paid.",
        "text",
        "supplier",
        "supplier name",
        "vendor",
        "payee",
        "paid to",
        "contact",
    ),
    _f(
        "category",
        "Cost category",
        "What kind of cost it is (rent, stock, wages...).",
        "text",
        "category",
        "cost category",
        "expense category",
        "account",
        "type",
    ),
    _f(
        "description",
        "Description",
        "What it was for.",
        "text",
        "description",
        "details",
        "item",
        "narrative",
        "memo",
    ),
)

CUSTOMERS = (
    _f(
        "name",
        "Name",
        "The customer's name.",
        "text",
        "name",
        "customer",
        "customer name",
        "full name",
        "client",
    ),
    _f(
        "email",
        "Email",
        "Their email address.",
        "email",
        "email",
        "email address",
        "customer email",
    ),
    _f(
        "postcode",
        "Postcode",
        "UK postcode, e.g. LS1 4AP.",
        "postcode",
        "postcode",
        "post code",
        "zip",
    ),
    _f(
        "customer_type",
        "Customer type",
        "e.g. Retail, Trade, Wholesale.",
        "text",
        "type",
        "customer type",
        "segment",
        "group",
    ),
    _f(
        "reference",
        "Customer number",
        _REFERENCE_HELP,
        "text",
        "customer id",
        "customer number",
        "account number",
        "account",
        "id",
        "reference",
    ),
)

SUPPLIERS = (
    _f(
        "name",
        "Supplier name",
        "Who you buy from.",
        "text",
        "name",
        "supplier",
        "supplier name",
        "vendor",
        "company",
    ),
    _f(
        "reference",
        "Supplier number",
        _REFERENCE_HELP,
        "text",
        "supplier id",
        "supplier number",
        "account number",
        "id",
        "reference",
    ),
)

PRODUCTS = (
    _f(
        "name",
        "Product name",
        "What you call it.",
        "text",
        "name",
        "product",
        "product name",
        "item",
        "item name",
        "description",
    ),
    _f(
        "sku",
        "Product code (SKU)",
        "Your stock code. Must be unique.",
        "text",
        "sku",
        "product code",
        "item code",
        "stock code",
        "code",
        "barcode",
    ),
    _f(
        "unit_price",
        "Selling price",
        "Price per unit.",
        "money",
        "price",
        "unit price",
        "selling price",
        "retail price",
        "rrp",
        "sell price",
    ),
    _f(
        "unit_cost",
        "Cost price",
        "What one unit costs you (excluding VAT).",
        "money",
        "cost",
        "unit cost",
        "cost price",
        "buy price",
        "purchase price",
    ),
    _f(
        "vat_rate",
        "VAT rate",
        "The VAT rate for the product (20, 5 or 0).",
        "rate",
        "vat rate",
        "tax rate",
        "vat %",
        "vat",
    ),
    _f(
        "category",
        "Category",
        "What kind of product it is.",
        "text",
        "category",
        "product category",
        "type",
        "range",
        "department",
    ),
)

STOCK_MOVEMENTS = (
    _f(
        "moved_on",
        "Date",
        "Day the stock moved (UK format).",
        "date",
        "date",
        "moved on",
        "movement date",
        "stock date",
    ),
    _f(
        "product",
        "Product name",
        "Which product moved.",
        "text",
        "product",
        "product name",
        "item",
        "item name",
        "description",
    ),
    _f(
        "sku",
        "Product code (SKU)",
        "Your stock code for the product.",
        "text",
        "sku",
        "product code",
        "item code",
        "stock code",
        "barcode",
    ),
    _f(
        "quantity",
        "Quantity",
        "How many. Out of stock is negative.",
        "quantity",
        "quantity",
        "qty",
        "units",
        "change",
        "movement",
    ),
    _f(
        "movement_kind",
        "Type of movement",
        "opening, delivery, sale, return, adjustment or "
        "write_off. If unmapped it's worked out from the sign of the quantity.",
        "choice",
        "type",
        "movement type",
        "kind",
        "reason",
    ),
    _f(
        "unit_cost",
        "Unit cost",
        "What one unit cost you (excluding VAT).",
        "money",
        "unit cost",
        "cost",
        "cost price",
    ),
    _f("notes", "Notes", "Anything else worth keeping.", "text", "notes", "note", "comment"),
    _f(
        "reference",
        "Movement reference",
        _REFERENCE_HELP,
        "text",
        "reference",
        "ref",
        "movement id",
        "id",
    ),
)

DATASET_FIELDS: dict[str, tuple[ImportField, ...]] = {
    "sales": SALES,
    "expenses": EXPENSES,
    "customers": CUSTOMERS,
    "suppliers": SUPPLIERS,
    "products": PRODUCTS,
    "stock_movements": STOCK_MOVEMENTS,
}

# Fields that must be mapped, and groups where at least one of them must be.
REQUIRED: dict[str, tuple[str, ...]] = {
    "sales": ("sold_on", "amount"),
    "expenses": ("spent_on", "amount"),
    "customers": (),
    "suppliers": ("name",),
    "products": ("name",),
    "stock_movements": ("moved_on", "quantity"),
}
ONE_OF: dict[str, tuple[tuple[str, ...], ...]] = {
    "customers": (("name", "email"),),
    "stock_movements": (("product", "sku"),),
}

# Money columns whose VAT treatment depends on the import's VAT options.
MONEY_AMOUNT_FIELD = {"sales": "amount", "expenses": "amount", "products": "unit_price"}
VAT_COLUMNS = {
    "sales": ("vat_amount", "vat_rate"),
    "expenses": ("vat_amount", "vat_rate"),
    "products": ("vat_rate",),
}
VAT_RATES = tuple(UK_VAT_RATES)  # ("0", "5", "20")

_INCLUDES_VAT = {"inc", "incl", "including", "gross"}
_EXCLUDES_VAT = {"ex", "excl", "excluding", "net"}


def field_keys(dataset: str) -> tuple[str, ...]:
    return tuple(f.key for f in DATASET_FIELDS[dataset])


def needs_vat_options(dataset: str) -> bool:
    return dataset in MONEY_AMOUNT_FIELD


def normalise(heading: str) -> str:
    """'Total (inc. VAT)' -> 'total inc vat': case, punctuation and spacing don't matter."""
    return re.sub(r"[^a-z0-9%]+", " ", heading.casefold()).strip()


def suggest_mapping(dataset: str, headers: list[str]) -> dict[str, str]:
    """Columns whose heading is a known name for a field. One column feeds one field; when
    two columns fit the same field the one further left in the file wins."""
    suggested: dict[str, str] = {}
    used: set[str] = set()
    for fld in DATASET_FIELDS[dataset]:
        for header in headers:
            if header not in used and normalise(header) in fld.aliases:
                suggested[fld.key] = header
                used.add(header)
                break
    return suggested


def suggest_vat_inclusive(heading: str | None) -> bool | None:
    """A heading like 'Total (inc VAT)' or 'Net' says which it is; anything else: no idea."""
    if not heading:
        return None
    words = set(normalise(heading).split())
    includes, excludes = bool(words & _INCLUDES_VAT), bool(words & _EXCLUDES_VAT)
    if includes == excludes:
        return None
    return includes


@dataclass(frozen=True)
class MappingIssue:
    code: str
    message: str
    field: str | None = None


def check_mapping(
    dataset: str,
    mapping: dict[str, str],
    options: dict,
    headers: list[str],
    *,
    require_answers: bool = True,
) -> list[MappingIssue]:
    """Everything wrong with a mapping + options for this file. Empty list = ready."""
    issues: list[MappingIssue] = []
    fields = {f.key: f for f in DATASET_FIELDS[dataset]}
    heads = set(headers)

    for key, header in mapping.items():
        if key not in fields:
            issues.append(
                MappingIssue("unknown_field", f"'{key}' isn't a field of {dataset}.", key)
            )
        elif header not in heads:
            issues.append(
                MappingIssue("unknown_column", f"This file has no column called '{header}'.", key)
            )

    seen: dict[str, str] = {}
    for key, header in mapping.items():
        if header in seen and key in fields and seen[header] in fields:
            issues.append(
                MappingIssue(
                    "column_used_twice",
                    f"The column '{header}' can only feed one field "
                    f"({fields[seen[header]].label} and {fields[key].label}).",
                    key,
                )
            )
        seen.setdefault(header, key)

    for key in REQUIRED[dataset]:
        if key not in mapping:
            issues.append(
                MappingIssue(
                    "required_field_missing", f"Choose the column for '{fields[key].label}'.", key
                )
            )
    for group in ONE_OF.get(dataset, ()):
        if not any(k in mapping for k in group):
            labels = " or ".join(f"'{fields[k].label}'" for k in group)
            issues.append(MappingIssue("one_of_missing", f"Choose a column for {labels}."))

    if needs_vat_options(dataset):
        issues += _check_vat_options(dataset, mapping, options, require_answers)
    elif options:
        issues.append(MappingIssue("options_not_applicable", f"{dataset} imports have no options."))
    return issues


def _check_vat_options(dataset, mapping, options, require_answers) -> list[MappingIssue]:
    issues = []
    unknown = set(options) - {"vat_inclusive", "default_vat_rate"}
    for key in sorted(unknown):
        issues.append(MappingIssue("unknown_option", f"'{key}' isn't an option."))
    inclusive, rate = options.get("vat_inclusive"), options.get("default_vat_rate")
    if rate is not None and rate not in VAT_RATES:
        issues.append(
            MappingIssue(
                "bad_vat_rate", "The VAT rate must be 20, 5 or 0 (UK rates).", "default_vat_rate"
            )
        )
    if not require_answers:
        return issues
    if MONEY_AMOUNT_FIELD[dataset] in mapping and inclusive is None:
        issues.append(
            MappingIssue(
                "vat_inclusive_required",
                "Say whether the amounts in this file include VAT.",
                "vat_inclusive",
            )
        )
    if (
        MONEY_AMOUNT_FIELD[dataset] in mapping
        and rate is None
        and not any(k in mapping for k in VAT_COLUMNS[dataset])
    ):
        issues.append(
            MappingIssue(
                "vat_rate_required",
                "This file has no VAT column, so say which VAT rate applies (20, 5 or 0).",
                "default_vat_rate",
            )
        )
    return issues
