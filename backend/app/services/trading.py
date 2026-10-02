"""Typing records in by hand, and looking after them afterwards (Phase 4 step 7).

Vyterlix must work with no files and no integrations (SLA clause 3.5), so everything a
file import can create can also be entered, corrected and removed here, by Owners and
Managers (data.manage). The session must be scoped to the organisation (CurrentTenant).

- Entered records have source "manual" and no import. Records that came from an import can be
  corrected or deleted too; they keep their source and import (undoing the import still
  removes them if they are still there).
- Amounts are entered positive with a `kind` (sale / refund, expense / credit) and stored
  with the sign, excluding VAT, with net + VAT = gross.
- Things that other records point at (customers, suppliers, products) can't be deleted while
  in use: archive them (is_active false) instead.
"""

import functools
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, StockMovement, Supplier
from app.schemas.trading import (
    CustomerCreate,
    CustomerOut,
    CustomerPatch,
    CustomersPage,
    ExpenseCreate,
    ExpenseOut,
    ExpensePatch,
    ExpensesPage,
    ProductCreate,
    ProductOut,
    ProductPatch,
    ProductsPage,
    SaleCreate,
    SaleLineIn,
    SaleLineOut,
    SaleOut,
    SalePatch,
    SalesPage,
    StockLevelOut,
    StockMovementCreate,
    StockMovementOut,
    StockPage,
    SupplierCreate,
    SupplierOut,
    SupplierPatch,
    SuppliersPage,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.money import VatError, vat_split
from app.services.value_parsers import round_pennies

UNIT_PRICE_PLACES = Decimal("0.0001")


# --- shared ------------------------------------------------


def _audit(db, tenant, meta, action: AuditAction, table: str, record_id: uuid.UUID, **details):
    record_audit(
        db,
        action,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type=table,
        target_id=record_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details=details or None,
    )


def _atomic(fn):
    """Run a create/update as one unit: if anything fails part-way (a rule broken, lines that
    don't add up) everything it did is undone, then commit once it has all worked."""

    @functools.wraps(fn)
    def wrapper(db, *args, **kwargs):
        with db.begin_nested():
            result = fn(db, *args, **kwargs)
        db.commit()
        return result

    return wrapper


def _invalid(field: str, code: str, message: str) -> AppError:
    return AppError(message, code=code, status_code=422, details={"field": field})


def _get(db: Session, model, record_id: uuid.UUID, what: str):
    record = db.get(model, record_id)  # scoped: another business's id is "not found"
    if record is None:
        raise NotFoundError(f"{what} not found")
    return record


def _must_exist(db: Session, model, record_id: uuid.UUID | None, field: str, what: str) -> None:
    if record_id is not None and db.get(model, record_id) is None:
        raise _invalid(field, "unknown_reference", f"That {what} doesn't exist in your business.")


def _must_be_listed(db: Session, item_id: uuid.UUID | None, kind: str, field: str, what: str):
    if item_id is None:
        return
    item = db.get(BusinessListItem, item_id)
    if item is None or item.kind != kind or not item.is_active:
        raise _invalid(
            field, "unknown_reference", f"Choose one of your {what} (it must be active)."
        )


def _page(
    db: Session, query: Select, order: Sequence[Any], limit: int, offset: int
) -> tuple[int, list]:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.scalars(query.order_by(*order).limit(limit).offset(offset)).all()
    return total, list(rows)


def _flush(db: Session, taken: dict[str, tuple[str, str]]) -> None:
    """Flush, turning a clash on a unique rule into a clear 409. `taken` maps constraint names
    to (error code, message)."""
    try:
        db.flush()
    except IntegrityError as exc:
        name = getattr(exc.orig.diag, "constraint_name", None) or ""
        for constraint, (code, message) in taken.items():
            if constraint in name:
                raise ConflictError(message, code=code) from exc
        raise


_REF_TAKEN = ("reference_taken", "You already have a record with that reference.")


def _delete(db: Session, tenant, meta, model, record_id, table: str, what: str, *, archivable):
    record = _get(db, model, record_id, what.capitalize())
    try:
        with db.begin_nested():
            db.delete(record)
            db.flush()
    except IntegrityError as exc:
        hint = " Archive it instead (set is_active to false)." if archivable else ""
        raise ConflictError(
            f"This {what} is used by other records, so it can't be deleted.{hint}", code="in_use"
        ) from exc
    _audit(db, tenant, meta, AuditAction.RECORD_DELETED, table, record_id)
    db.commit()


def _apply(record, body, nullable: set[str], renames: dict[str, str] | None = None) -> None:
    """Copy the fields the caller sent onto the record. Null clears an optional field; it
    is refused for a required one."""
    renames = renames or {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        if value is None and name not in nullable:
            raise _invalid(name, "cannot_be_blank", "That can't be left blank.")
        setattr(record, renames.get(name, name), value)


# --- money ------------------------------------------------


def _amounts(
    amount: Decimal,
    includes_vat: bool,
    vat_rate: str | None,
    vat_amount: Decimal | None,
    *,
    negative: bool,
) -> tuple[Decimal, Decimal, Decimal]:
    try:
        net, vat, gross = vat_split(
            amount,
            includes_vat=includes_vat,
            rate=Decimal(vat_rate) if vat_rate is not None else None,
            vat_amount=vat_amount,
        )
    except VatError as exc:
        raise _invalid("vat_amount", exc.code, exc.message) from None
    sign = -1 if negative else 1
    return sign * net, sign * vat, sign * gross


# --- sales ------------------------------------------------


def _lines_out(lines: Sequence[SaleLine]) -> list[SaleLineOut]:
    return [SaleLineOut.model_validate(line) for line in sorted(lines, key=lambda x: x.id.int)]


def _sale_out(db: Session, sale: Sale) -> SaleOut:
    lines = db.scalars(select(SaleLine).where(SaleLine.sale_id == sale.id)).all()
    return SaleOut.model_validate(sale).model_copy(update={"lines": _lines_out(lines)})


def _make_lines(db: Session, sale: Sale, lines: list[SaleLineIn]) -> list[SaleLine]:
    """Line detail must add up to the sale (excluding VAT). Each line's VAT is its share of the
    sale's VAT, to the penny, the last line taking any rounding."""
    if not lines:
        return []
    net_total, vat_total = abs(sale.net_amount), abs(sale.vat_amount)
    got = sum((line.net_amount for line in lines), Decimal(0))
    if got != net_total:
        raise AppError(
            f"The lines add up to £{got:.2f} but the sale is £{net_total:.2f} excluding VAT.",
            code="lines_do_not_add_up",
            status_code=422,
            details={"field": "lines", "expected": str(net_total), "got": str(got)},
        )
    sign = -1 if sale.kind == "refund" else 1
    made, vat_left = [], vat_total
    for i, line in enumerate(lines):
        _must_exist(db, Product, line.product_id, "lines", "product")
        last = i == len(lines) - 1
        if last:
            vat = vat_left
        elif net_total:
            vat = round_pennies(vat_total * line.net_amount / net_total)
        else:
            vat = Decimal(0)
        vat_left -= vat
        unit = (line.net_amount / line.quantity).quantize(UNIT_PRICE_PLACES)
        made.append(
            SaleLine(
                sale_id=sale.id,
                product_id=line.product_id,
                description=line.description,
                quantity=sign * line.quantity,
                unit_price_ex_vat=unit,
                net_amount=sign * line.net_amount,
                vat_amount=sign * vat,
                cost_amount=line.cost_amount,
            )
        )
    return made


@_atomic
def create_sale(db: Session, tenant, body: SaleCreate, meta: RequestMeta) -> SaleOut:
    _must_exist(db, Customer, body.customer_id, "customer_id", "customer")
    _must_be_listed(
        db, body.sales_channel_id, "sales_channel", "sales_channel_id", "sales channels"
    )
    net, vat, gross = _amounts(
        body.amount,
        body.amount_includes_vat,
        body.vat_rate,
        body.vat_amount,
        negative=body.kind == "refund",
    )
    sale = Sale(
        kind=body.kind,
        sold_on=body.sold_on,
        customer_id=body.customer_id,
        sales_channel_id=body.sales_channel_id,
        net_amount=net,
        vat_amount=vat,
        gross_amount=gross,
        discount_amount=body.discount,
        notes=body.notes,
        source="manual",
        source_ref=body.reference,
    )
    db.add(sale)
    _flush(db, {"uq_sales_source_ref": _REF_TAKEN})
    db.add_all(_make_lines(db, sale, body.lines))
    db.flush()
    _audit(db, tenant, meta, AuditAction.RECORD_CREATED, "sales", sale.id, kind=sale.kind)
    return _sale_out(db, sale)


def get_sale(db: Session, sale_id: uuid.UUID) -> SaleOut:
    return _sale_out(db, _get(db, Sale, sale_id, "Sale"))


@_atomic
def update_sale(
    db: Session, tenant, sale_id: uuid.UUID, body: SalePatch, meta: RequestMeta
) -> SaleOut:
    sale = _get(db, Sale, sale_id, "Sale")
    sent = body.model_fields_set
    money = bool(sent & {"amount", "amount_includes_vat", "vat_rate", "vat_amount"})
    if "kind" in sent and body.kind is None:
        raise _invalid("kind", "cannot_be_blank", "That can't be left blank.")
    if "kind" in sent and body.kind != sale.kind and not money:
        raise _invalid(
            "amount",
            "kind_needs_amount",
            "To change a sale into a refund (or back), send the amount again.",
        )
    kind = body.kind if "kind" in sent else sale.kind
    if "customer_id" in sent:
        _must_exist(db, Customer, body.customer_id, "customer_id", "customer")
    if "sales_channel_id" in sent:
        _must_be_listed(
            db, body.sales_channel_id, "sales_channel", "sales_channel_id", "sales channels"
        )
    existing_lines = db.scalars(select(SaleLine).where(SaleLine.sale_id == sale.id)).all()
    if money and existing_lines and body.lines is None:
        raise _invalid(
            "lines",
            "lines_need_updating",
            "This sale has line detail. Send the lines again so they add up to the new amount.",
        )

    for name in sent - {"amount", "amount_includes_vat", "vat_rate", "vat_amount", "lines"}:
        value = getattr(body, name)
        if value is None and name not in {"customer_id", "sales_channel_id", "reference", "notes"}:
            raise _invalid(name, "cannot_be_blank", "That can't be left blank.")
        setattr(
            sale, {"discount": "discount_amount", "reference": "source_ref"}.get(name, name), value
        )
    if money:
        sale.net_amount, sale.vat_amount, sale.gross_amount = _amounts(
            body.amount,
            body.amount_includes_vat,
            body.vat_rate,
            body.vat_amount,
            negative=kind == "refund",
        )
    if body.lines is not None:
        db.execute(delete(SaleLine).where(SaleLine.sale_id == sale.id))
        db.add_all(_make_lines(db, sale, body.lines))
    _flush(db, {"uq_sales_source_ref": _REF_TAKEN})
    _audit(db, tenant, meta, AuditAction.RECORD_UPDATED, "sales", sale.id, fields=sorted(sent))
    db.refresh(sale)
    return _sale_out(db, sale)


def delete_sale(db: Session, tenant, sale_id: uuid.UUID, meta: RequestMeta) -> None:
    _delete(db, tenant, meta, Sale, sale_id, "sales", "sale", archivable=False)


def list_sales(
    db: Session,
    *,
    date_from: date | None,
    date_to: date | None,
    kind: str | None,
    customer_id: uuid.UUID | None,
    sales_channel_id: uuid.UUID | None,
    source: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> SalesPage:
    query = select(Sale)
    if date_from:
        query = query.where(Sale.sold_on >= date_from)
    if date_to:
        query = query.where(Sale.sold_on <= date_to)
    if kind:
        query = query.where(Sale.kind == kind)
    if customer_id:
        query = query.where(Sale.customer_id == customer_id)
    if sales_channel_id:
        query = query.where(Sale.sales_channel_id == sales_channel_id)
    if source:
        query = query.where(Sale.source == source)
    if q:
        query = query.where(Sale.source_ref.ilike(f"%{_like(q)}%", escape="\\"))
    total, sales = _page(
        db, query, (Sale.sold_on.desc(), Sale.created_at.desc(), Sale.id), limit, offset
    )
    lines: dict[uuid.UUID, list[SaleLine]] = {}
    if sales:
        for line in db.scalars(select(SaleLine).where(SaleLine.sale_id.in_([s.id for s in sales]))):
            lines.setdefault(line.sale_id, []).append(line)
    items = [
        SaleOut.model_validate(s).model_copy(update={"lines": _lines_out(lines.get(s.id, []))})
        for s in sales
    ]
    return SalesPage(total=total, items=items)


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- expenses ------------------------------------------------


@_atomic
def create_expense(db: Session, tenant, body: ExpenseCreate, meta: RequestMeta) -> ExpenseOut:
    _must_exist(db, Supplier, body.supplier_id, "supplier_id", "supplier")
    _must_be_listed(
        db, body.cost_category_id, "cost_category", "cost_category_id", "cost categories"
    )
    net, vat, gross = _amounts(
        body.amount,
        body.amount_includes_vat,
        body.vat_rate,
        body.vat_amount,
        negative=body.kind == "credit",
    )
    expense = Expense(
        kind=body.kind,
        spent_on=body.spent_on,
        supplier_id=body.supplier_id,
        cost_category_id=body.cost_category_id,
        description=body.description,
        net_amount=net,
        vat_amount=vat,
        gross_amount=gross,
        source="manual",
        source_ref=body.reference,
    )
    db.add(expense)
    _flush(db, {"uq_expenses_source_ref": _REF_TAKEN})
    _audit(db, tenant, meta, AuditAction.RECORD_CREATED, "expenses", expense.id, kind=expense.kind)
    return ExpenseOut.model_validate(expense)


def get_expense(db: Session, expense_id: uuid.UUID) -> ExpenseOut:
    return ExpenseOut.model_validate(_get(db, Expense, expense_id, "Expense"))


@_atomic
def update_expense(
    db: Session, tenant, expense_id: uuid.UUID, body: ExpensePatch, meta: RequestMeta
) -> ExpenseOut:
    expense = _get(db, Expense, expense_id, "Expense")
    sent = body.model_fields_set
    money = bool(sent & {"amount", "amount_includes_vat", "vat_rate", "vat_amount"})
    if "kind" in sent and body.kind is None:
        raise _invalid("kind", "cannot_be_blank", "That can't be left blank.")
    if "kind" in sent and body.kind != expense.kind and not money:
        raise _invalid(
            "amount",
            "kind_needs_amount",
            "To change an expense into a credit (or back), send the amount again.",
        )
    kind = body.kind if "kind" in sent else expense.kind
    if "supplier_id" in sent:
        _must_exist(db, Supplier, body.supplier_id, "supplier_id", "supplier")
    if "cost_category_id" in sent:
        _must_be_listed(
            db, body.cost_category_id, "cost_category", "cost_category_id", "cost categories"
        )
    for name in sent - {"amount", "amount_includes_vat", "vat_rate", "vat_amount"}:
        value = getattr(body, name)
        if value is None and name not in {
            "supplier_id",
            "cost_category_id",
            "description",
            "reference",
        }:
            raise _invalid(name, "cannot_be_blank", "That can't be left blank.")
        setattr(expense, "source_ref" if name == "reference" else name, value)
    if money:
        expense.net_amount, expense.vat_amount, expense.gross_amount = _amounts(
            body.amount,
            body.amount_includes_vat,
            body.vat_rate,
            body.vat_amount,
            negative=kind == "credit",
        )
    _flush(db, {"uq_expenses_source_ref": _REF_TAKEN})
    _audit(
        db, tenant, meta, AuditAction.RECORD_UPDATED, "expenses", expense.id, fields=sorted(sent)
    )
    db.refresh(expense)
    return ExpenseOut.model_validate(expense)


def delete_expense(db: Session, tenant, expense_id: uuid.UUID, meta: RequestMeta) -> None:
    _delete(db, tenant, meta, Expense, expense_id, "expenses", "expense", archivable=False)


def list_expenses(
    db: Session,
    *,
    date_from: date | None,
    date_to: date | None,
    kind: str | None,
    supplier_id: uuid.UUID | None,
    cost_category_id: uuid.UUID | None,
    source: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> ExpensesPage:
    query = select(Expense)
    if date_from:
        query = query.where(Expense.spent_on >= date_from)
    if date_to:
        query = query.where(Expense.spent_on <= date_to)
    if kind:
        query = query.where(Expense.kind == kind)
    if supplier_id:
        query = query.where(Expense.supplier_id == supplier_id)
    if cost_category_id:
        query = query.where(Expense.cost_category_id == cost_category_id)
    if source:
        query = query.where(Expense.source == source)
    if q:
        pattern = f"%{_like(q)}%"
        query = query.where(
            or_(
                Expense.source_ref.ilike(pattern, escape="\\"),
                Expense.description.ilike(pattern, escape="\\"),
            )
        )
    total, rows = _page(
        db, query, (Expense.spent_on.desc(), Expense.created_at.desc(), Expense.id), limit, offset
    )
    return ExpensesPage(total=total, items=[ExpenseOut.model_validate(r) for r in rows])


# --- customers ------------------------------------------------


@_atomic
def create_customer(db: Session, tenant, body: CustomerCreate, meta: RequestMeta) -> CustomerOut:
    _must_be_listed(
        db, body.customer_type_id, "customer_type", "customer_type_id", "customer types"
    )
    customer = Customer(**body.model_dump(), source="manual")
    db.add(customer)
    db.flush()
    _audit(db, tenant, meta, AuditAction.RECORD_CREATED, "customers", customer.id)
    return CustomerOut.model_validate(customer)


def get_customer(db: Session, customer_id: uuid.UUID) -> CustomerOut:
    return CustomerOut.model_validate(_get(db, Customer, customer_id, "Customer"))


@_atomic
def update_customer(
    db: Session, tenant, customer_id: uuid.UUID, body: CustomerPatch, meta: RequestMeta
) -> CustomerOut:
    customer = _get(db, Customer, customer_id, "Customer")
    if "customer_type_id" in body.model_fields_set:
        _must_be_listed(
            db, body.customer_type_id, "customer_type", "customer_type_id", "customer types"
        )
    _apply(customer, body, nullable={"name", "email", "postcode", "customer_type_id"})
    if customer.name is None and customer.email is None:
        raise _invalid("name", "someone_needed", "A customer needs a name or an email address.")
    db.flush()
    _audit(
        db,
        tenant,
        meta,
        AuditAction.RECORD_UPDATED,
        "customers",
        customer.id,
        fields=sorted(body.model_fields_set),
    )
    db.refresh(customer)
    return CustomerOut.model_validate(customer)


def delete_customer(db: Session, tenant, customer_id: uuid.UUID, meta: RequestMeta) -> None:
    _delete(db, tenant, meta, Customer, customer_id, "customers", "customer", archivable=True)


def list_customers(
    db: Session, *, q: str | None, active: bool | None, limit: int, offset: int
) -> CustomersPage:
    query = select(Customer)
    if active is not None:
        query = query.where(Customer.is_active == active)
    if q:
        pattern = f"%{_like(q)}%"
        query = query.where(
            or_(
                Customer.name.ilike(pattern, escape="\\"),
                Customer.email.ilike(pattern, escape="\\"),
            )
        )
    total, rows = _page(
        db,
        query,
        (func.lower(func.coalesce(Customer.name, Customer.email)), Customer.id),
        limit,
        offset,
    )
    return CustomersPage(total=total, items=[CustomerOut.model_validate(r) for r in rows])


# --- suppliers ------------------------------------------------


@_atomic
def create_supplier(db: Session, tenant, body: SupplierCreate, meta: RequestMeta) -> SupplierOut:
    supplier = Supplier(**body.model_dump(), source="manual")
    db.add(supplier)
    db.flush()
    _audit(db, tenant, meta, AuditAction.RECORD_CREATED, "suppliers", supplier.id)
    return SupplierOut.model_validate(supplier)


def get_supplier(db: Session, supplier_id: uuid.UUID) -> SupplierOut:
    return SupplierOut.model_validate(_get(db, Supplier, supplier_id, "Supplier"))


@_atomic
def update_supplier(
    db: Session, tenant, supplier_id: uuid.UUID, body: SupplierPatch, meta: RequestMeta
) -> SupplierOut:
    supplier = _get(db, Supplier, supplier_id, "Supplier")
    _apply(supplier, body, nullable=set())
    db.flush()
    _audit(
        db,
        tenant,
        meta,
        AuditAction.RECORD_UPDATED,
        "suppliers",
        supplier.id,
        fields=sorted(body.model_fields_set),
    )
    db.refresh(supplier)
    return SupplierOut.model_validate(supplier)


def delete_supplier(db: Session, tenant, supplier_id: uuid.UUID, meta: RequestMeta) -> None:
    _delete(db, tenant, meta, Supplier, supplier_id, "suppliers", "supplier", archivable=True)


def list_suppliers(
    db: Session, *, q: str | None, active: bool | None, limit: int, offset: int
) -> SuppliersPage:
    query = select(Supplier)
    if active is not None:
        query = query.where(Supplier.is_active == active)
    if q:
        query = query.where(Supplier.name.ilike(f"%{_like(q)}%", escape="\\"))
    total, rows = _page(db, query, (func.lower(Supplier.name), Supplier.id), limit, offset)
    return SuppliersPage(total=total, items=[SupplierOut.model_validate(r) for r in rows])


# --- products ------------------------------------------------

_SKU_TAKEN = ("sku_taken", "You already have a product with that code (SKU).")


@_atomic
def create_product(db: Session, tenant, body: ProductCreate, meta: RequestMeta) -> ProductOut:
    _must_be_listed(db, body.offering_id, "offering", "offering_id", "offerings")
    data = body.model_dump()
    if data["vat_rate"] is not None:
        data["vat_rate"] = Decimal(data["vat_rate"])
    product = Product(**data, source="manual")
    db.add(product)
    _flush(db, {"uq_products_sku": _SKU_TAKEN})
    _audit(db, tenant, meta, AuditAction.RECORD_CREATED, "products", product.id)
    return ProductOut.model_validate(product)


def get_product(db: Session, product_id: uuid.UUID) -> ProductOut:
    return ProductOut.model_validate(_get(db, Product, product_id, "Product"))


@_atomic
def update_product(
    db: Session, tenant, product_id: uuid.UUID, body: ProductPatch, meta: RequestMeta
) -> ProductOut:
    product = _get(db, Product, product_id, "Product")
    if "offering_id" in body.model_fields_set:
        _must_be_listed(db, body.offering_id, "offering", "offering_id", "offerings")
    _apply(
        product,
        body,
        nullable={"sku", "offering_id", "unit_price_ex_vat", "unit_cost", "vat_rate"},
    )
    if isinstance(product.vat_rate, str):
        product.vat_rate = Decimal(product.vat_rate)
    _flush(db, {"uq_products_sku": _SKU_TAKEN})
    _audit(
        db,
        tenant,
        meta,
        AuditAction.RECORD_UPDATED,
        "products",
        product.id,
        fields=sorted(body.model_fields_set),
    )
    db.refresh(product)
    return ProductOut.model_validate(product)


def delete_product(db: Session, tenant, product_id: uuid.UUID, meta: RequestMeta) -> None:
    _delete(db, tenant, meta, Product, product_id, "products", "product", archivable=True)


def list_products(
    db: Session, *, q: str | None, active: bool | None, limit: int, offset: int
) -> ProductsPage:
    query = select(Product)
    if active is not None:
        query = query.where(Product.is_active == active)
    if q:
        pattern = f"%{_like(q)}%"
        query = query.where(
            or_(Product.name.ilike(pattern, escape="\\"), Product.sku.ilike(pattern, escape="\\"))
        )
    total, rows = _page(db, query, (func.lower(Product.name), Product.id), limit, offset)
    return ProductsPage(total=total, items=[ProductOut.model_validate(r) for r in rows])


# --- stock ------------------------------------------------


@_atomic
def create_stock_movement(
    db: Session, tenant, body: StockMovementCreate, meta: RequestMeta
) -> StockMovementOut:
    _must_exist(db, Product, body.product_id, "product_id", "product")
    movement = StockMovement(**body.model_dump(), source="manual")
    db.add(movement)
    db.flush()
    _audit(
        db,
        tenant,
        meta,
        AuditAction.RECORD_CREATED,
        "stock_movements",
        movement.id,
        kind=movement.kind,
    )
    return StockMovementOut.model_validate(movement)


def delete_stock_movement(db: Session, tenant, movement_id: uuid.UUID, meta: RequestMeta) -> None:
    _delete(
        db,
        tenant,
        meta,
        StockMovement,
        movement_id,
        "stock_movements",
        "stock movement",
        archivable=False,
    )


def list_stock_movements(
    db: Session,
    *,
    product_id: uuid.UUID | None,
    kind: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> StockPage:
    query = select(StockMovement)
    if product_id:
        query = query.where(StockMovement.product_id == product_id)
    if kind:
        query = query.where(StockMovement.kind == kind)
    if date_from:
        query = query.where(StockMovement.moved_on >= date_from)
    if date_to:
        query = query.where(StockMovement.moved_on <= date_to)
    total, rows = _page(
        db,
        query,
        (StockMovement.moved_on.desc(), StockMovement.created_at.desc(), StockMovement.id),
        limit,
        offset,
    )
    return StockPage(total=total, items=[StockMovementOut.model_validate(r) for r in rows])


def stock_level(db: Session, product_id: uuid.UUID, as_of: date) -> StockLevelOut:
    _get(db, Product, product_id, "Product")
    quantity = db.scalar(
        select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.product_id == product_id, StockMovement.moved_on <= as_of
        )
    )
    return StockLevelOut(product_id=product_id, as_of=as_of, quantity=quantity)
