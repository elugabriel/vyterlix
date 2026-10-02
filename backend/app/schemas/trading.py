"""Typing records in by hand: sales, expenses, customers, suppliers, products, stock.

UK rules as everywhere: pounds sterling, UK VAT rates (20, 5, 0), UK postcodes, dates
that exist. Amounts are entered positive; `kind` says refund / credit. Whether the amount
includes VAT is always stated, never assumed.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from app.core.uk import today_uk
from app.models.data import SALE_KINDS, STOCK_MOVEMENT_KINDS
from app.services.value_parsers import (
    EARLIEST_DATE,
    CellError,
    parse_email,
    parse_postcode,
)

Pounds = Annotated[
    Decimal, Field(ge=0, lt=Decimal("1000000000000"), max_digits=16, decimal_places=2)
]
PositivePounds = Annotated[
    Decimal, Field(gt=0, lt=Decimal("1000000000000"), max_digits=16, decimal_places=2)
]
UnitMoney = Annotated[
    Decimal, Field(ge=0, lt=Decimal("1000000000000"), max_digits=16, decimal_places=4)
]
Quantity = Annotated[Decimal, Field(lt=Decimal("1000000000"), max_digits=14, decimal_places=4)]
PositiveQuantity = Annotated[
    Decimal, Field(gt=0, lt=Decimal("1000000000"), max_digits=14, decimal_places=4)
]
VatRate = Literal["0", "5", "20"]


def _pounds_and_pence(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


# Money in responses: pounds and pence ("10.00"), whatever precision the database keeps.
Money2 = Annotated[Decimal, PlainSerializer(_pounds_and_pence, return_type=str, when_used="json")]


def _tidy(value: str) -> str:
    return " ".join(value.split())  # "Jo   Bloggs" -> "Jo Bloggs", like the file import


def _text(max_length: int):
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length),
        AfterValidator(_tidy),
    ]


Name = _text(200)
ShortName = _text(100)
Reference = _text(200)
Description = _text(300)
Notes = _text(2000)


def _trading_date(value: date) -> date:
    if value < EARLIEST_DATE:
        raise ValueError("That date is before 1990, which looks wrong")
    if (value - today_uk()).days > 1:
        raise ValueError("That date is in the future")
    return value


TradingDate = Annotated[date, AfterValidator(_trading_date)]


def _email(value: str) -> str:
    try:
        return parse_email(value)
    except CellError as exc:
        raise ValueError(exc.message) from None


def _postcode(value: str) -> str:
    try:
        return parse_postcode(value)
    except CellError:
        raise ValueError("Enter a UK postcode, for example LS1 4AP") from None


Email = Annotated[str, StringConstraints(max_length=320), AfterValidator(_email)]
Postcode = Annotated[str, AfterValidator(_postcode)]


class VatFields(BaseModel):
    """How much, and the VAT on it. Give the VAT as a rate or as an amount, not both."""

    amount: PositivePounds
    amount_includes_vat: bool
    vat_rate: VatRate | None = None
    vat_amount: Pounds | None = None

    @model_validator(mode="after")
    def _one_way_to_say_the_vat(self) -> "VatFields":
        if (self.vat_rate is None) == (self.vat_amount is None):
            raise ValueError("Give either vat_rate or vat_amount (exactly one)")
        return self


# --- sales ------------------------------------------------


class SaleLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID | None = None
    description: Description | None = None
    quantity: PositiveQuantity
    net_amount: Pounds  # excluding VAT
    cost_amount: Pounds | None = None  # what the goods cost you; leave out if unknown

    @model_validator(mode="after")
    def _says_what_was_sold(self) -> "SaleLineIn":
        if self.product_id is None and self.description is None:
            raise ValueError("Say what was sold: a product or a description")
        return self


class SaleCreate(VatFields):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[SALE_KINDS] = "sale"  # type: ignore[valid-type]
    sold_on: TradingDate
    discount: Pounds = Decimal(0)
    customer_id: uuid.UUID | None = None
    sales_channel_id: uuid.UUID | None = None
    reference: Reference | None = None  # e.g. an invoice number, unique per business
    notes: Notes | None = None
    lines: list[SaleLineIn] = Field(default_factory=list, max_length=100)


class SalePatch(BaseModel):
    """Change some fields. To change any money field, send all of amount,
    amount_includes_vat and one of vat_rate / vat_amount together."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[SALE_KINDS] | None = None  # type: ignore[valid-type]
    sold_on: TradingDate | None = None
    amount: PositivePounds | None = None
    amount_includes_vat: bool | None = None
    vat_rate: VatRate | None = None
    vat_amount: Pounds | None = None
    discount: Pounds | None = None
    customer_id: uuid.UUID | None = None
    sales_channel_id: uuid.UUID | None = None
    reference: Reference | None = None
    notes: Notes | None = None
    lines: list[SaleLineIn] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _money_changes_together(self) -> "SalePatch":
        return _check_money_patch(self)


class SaleLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None
    description: str | None
    quantity: Decimal
    unit_price_ex_vat: Decimal | None
    net_amount: Money2
    vat_amount: Money2
    cost_amount: Money2 | None


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    kind: str
    sold_on: date
    customer_id: uuid.UUID | None
    sales_channel_id: uuid.UUID | None
    net_amount: Money2
    vat_amount: Money2
    gross_amount: Money2
    discount_amount: Money2
    currency: str
    reference: str | None = Field(default=None, validation_alias="source_ref")
    notes: str | None
    source: str
    import_id: uuid.UUID | None
    created_at: datetime
    lines: list[SaleLineOut] = []


# --- expenses ------------------------------------------------


class ExpenseCreate(VatFields):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["expense", "credit"] = "expense"
    spent_on: TradingDate
    supplier_id: uuid.UUID | None = None
    cost_category_id: uuid.UUID | None = None
    description: Description | None = None
    reference: Reference | None = None


class ExpensePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["expense", "credit"] | None = None
    spent_on: TradingDate | None = None
    amount: PositivePounds | None = None
    amount_includes_vat: bool | None = None
    vat_rate: VatRate | None = None
    vat_amount: Pounds | None = None
    supplier_id: uuid.UUID | None = None
    cost_category_id: uuid.UUID | None = None
    description: Description | None = None
    reference: Reference | None = None

    @model_validator(mode="after")
    def _money_changes_together(self) -> "ExpensePatch":
        return _check_money_patch(self)


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    kind: str
    spent_on: date
    supplier_id: uuid.UUID | None
    cost_category_id: uuid.UUID | None
    description: str | None
    net_amount: Money2
    vat_amount: Money2
    gross_amount: Money2
    currency: str
    reference: str | None = Field(default=None, validation_alias="source_ref")
    source: str
    import_id: uuid.UUID | None
    created_at: datetime


def _check_money_patch(body: Any) -> Any:
    fields = body.model_fields_set & {"amount", "amount_includes_vat", "vat_rate", "vat_amount"}
    if not fields:
        return body
    if "amount" not in fields or "amount_includes_vat" not in fields:
        raise ValueError("To change the money, send amount and amount_includes_vat too")
    if (body.vat_rate is None) == (body.vat_amount is None):
        raise ValueError("To change the money, give either vat_rate or vat_amount (exactly one)")
    return body


# --- customers, suppliers, products ------------------------------------------------


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    email: Email | None = None
    postcode: Postcode | None = None
    customer_type_id: uuid.UUID | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _someone(self) -> "CustomerCreate":
        if self.name is None and self.email is None:
            raise ValueError("Give a name or an email address")
        return self


class CustomerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    email: Email | None = None
    postcode: Postcode | None = None
    customer_type_id: uuid.UUID | None = None
    is_active: bool | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    email: str | None
    postcode: str | None
    customer_type_id: uuid.UUID | None
    is_active: bool
    source: str
    import_id: uuid.UUID | None
    created_at: datetime


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    is_active: bool = True


class SupplierPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    is_active: bool | None = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    source: str
    import_id: uuid.UUID | None
    created_at: datetime


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    sku: _text(100) | None = None
    offering_id: uuid.UUID | None = None
    unit_price_ex_vat: UnitMoney | None = None  # excluding VAT
    unit_cost: UnitMoney | None = None
    vat_rate: VatRate | None = None
    is_active: bool = True


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    sku: _text(100) | None = None
    offering_id: uuid.UUID | None = None
    unit_price_ex_vat: UnitMoney | None = None
    unit_cost: UnitMoney | None = None
    vat_rate: VatRate | None = None
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sku: str | None
    offering_id: uuid.UUID | None
    unit_price_ex_vat: Decimal | None
    unit_cost: Decimal | None
    vat_rate: Decimal | None
    is_active: bool
    source: str
    import_id: uuid.UUID | None
    created_at: datetime


# --- stock ------------------------------------------------


class StockMovementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    moved_on: TradingDate
    kind: Literal[STOCK_MOVEMENT_KINDS]  # type: ignore[valid-type]
    # As stored: more stock is positive, less is negative. delivery/return > 0,
    # sale/write_off < 0, opening >= 0, adjustment either way (not zero).
    quantity: Quantity
    unit_cost: UnitMoney | None = None
    notes: Description | None = None

    @model_validator(mode="after")
    def _direction_matches_kind(self) -> "StockMovementCreate":
        ok = {
            "opening": self.quantity >= 0,
            "delivery": self.quantity > 0,
            "return": self.quantity > 0,
            "sale": self.quantity < 0,
            "write_off": self.quantity < 0,
            "adjustment": self.quantity != 0,
        }[self.kind]
        if not ok:
            needs = {
                "opening": "zero or more",
                "delivery": "more than zero",
                "return": "more than zero",
                "sale": "less than zero",
                "write_off": "less than zero",
                "adjustment": "not zero",
            }[self.kind]
            raise ValueError(f"A '{self.kind}' movement needs a quantity that is {needs}")
        return self


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    product_id: uuid.UUID
    moved_on: date
    kind: str
    quantity: Decimal
    unit_cost: Decimal | None
    notes: str | None
    source: str
    import_id: uuid.UUID | None
    created_at: datetime


class StockLevelOut(BaseModel):
    product_id: uuid.UUID
    as_of: date
    quantity: Decimal  # the sum of every movement up to and including that day


# --- lists ------------------------------------------------


class Page(BaseModel):
    total: int


class SalesPage(Page):
    items: list[SaleOut]


class ExpensesPage(Page):
    items: list[ExpenseOut]


class CustomersPage(Page):
    items: list[CustomerOut]


class SuppliersPage(Page):
    items: list[SupplierOut]


class ProductsPage(Page):
    items: list[ProductOut]


class StockPage(Page):
    items: list[StockMovementOut]
