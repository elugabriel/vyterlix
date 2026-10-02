"""Typing records in by hand (Phase 4 step 7). Owners and Managers only: these lists hold
the business's customers and takings, so Viewers don't get them (they see insights)."""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.core.uk import today_uk
from app.models.data import DATA_SOURCES, STOCK_MOVEMENT_KINDS
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
from app.services import trading as svc

DataManager = Annotated[Tenant, Depends(require_permission(Perm.DATA_MANAGE))]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
Search = Annotated[str | None, Query(max_length=100)]
Source = Annotated[str | None, Query(pattern=f"^({'|'.join(DATA_SOURCES)})$")]
NoContent = status.HTTP_204_NO_CONTENT


def _router(path: str) -> APIRouter:
    return APIRouter(prefix=f"/organizations/{{organization_id}}/{path}", tags=["records"])


sales_router = _router("sales")
expenses_router = _router("expenses")
customers_router = _router("customers")
suppliers_router = _router("suppliers")
products_router = _router("products")
stock_router = _router("stock-movements")


# --- sales -------------------------------------------------------------------------------------


@sales_router.post("", status_code=status.HTTP_201_CREATED, response_model=SaleOut)
def create_sale(body: SaleCreate, tenant: DataManager, db: DB, meta: Meta):
    return svc.create_sale(db, tenant, body, meta)


@sales_router.get("", response_model=SalesPage)
def list_sales(
    tenant: DataManager,
    db: DB,
    date_from: date | None = None,
    date_to: date | None = None,
    kind: Literal["sale", "refund"] | None = None,
    customer_id: uuid.UUID | None = None,
    sales_channel_id: uuid.UUID | None = None,
    source: Source = None,
    q: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    """Newest first. `q` searches the reference (e.g. invoice number)."""
    return svc.list_sales(
        db,
        date_from=date_from,
        date_to=date_to,
        kind=kind,
        customer_id=customer_id,
        sales_channel_id=sales_channel_id,
        source=source,
        q=q,
        limit=limit,
        offset=offset,
    )


@sales_router.get("/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: uuid.UUID, tenant: DataManager, db: DB):
    return svc.get_sale(db, sale_id)


@sales_router.patch("/{sale_id}", response_model=SaleOut)
def update_sale(sale_id: uuid.UUID, body: SalePatch, tenant: DataManager, db: DB, meta: Meta):
    return svc.update_sale(db, tenant, sale_id, body, meta)


@sales_router.delete("/{sale_id}", status_code=NoContent)
def delete_sale(sale_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    svc.delete_sale(db, tenant, sale_id, meta)
    return Response(status_code=NoContent)


# --- expenses ----------------------------------------------------------------------------------


@expenses_router.post("", status_code=status.HTTP_201_CREATED, response_model=ExpenseOut)
def create_expense(body: ExpenseCreate, tenant: DataManager, db: DB, meta: Meta):
    return svc.create_expense(db, tenant, body, meta)


@expenses_router.get("", response_model=ExpensesPage)
def list_expenses(
    tenant: DataManager,
    db: DB,
    date_from: date | None = None,
    date_to: date | None = None,
    kind: Literal["expense", "credit"] | None = None,
    supplier_id: uuid.UUID | None = None,
    cost_category_id: uuid.UUID | None = None,
    source: Source = None,
    q: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return svc.list_expenses(
        db,
        date_from=date_from,
        date_to=date_to,
        kind=kind,
        supplier_id=supplier_id,
        cost_category_id=cost_category_id,
        source=source,
        q=q,
        limit=limit,
        offset=offset,
    )


@expenses_router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: uuid.UUID, tenant: DataManager, db: DB):
    return svc.get_expense(db, expense_id)


@expenses_router.patch("/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: uuid.UUID, body: ExpensePatch, tenant: DataManager, db: DB, meta: Meta
):
    return svc.update_expense(db, tenant, expense_id, body, meta)


@expenses_router.delete("/{expense_id}", status_code=NoContent)
def delete_expense(expense_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    svc.delete_expense(db, tenant, expense_id, meta)
    return Response(status_code=NoContent)


# --- customers ---------------------------------------------------------------------------------


@customers_router.post("", status_code=status.HTTP_201_CREATED, response_model=CustomerOut)
def create_customer(body: CustomerCreate, tenant: DataManager, db: DB, meta: Meta):
    return svc.create_customer(db, tenant, body, meta)


@customers_router.get("", response_model=CustomersPage)
def list_customers(
    tenant: DataManager,
    db: DB,
    q: Search = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return svc.list_customers(db, q=q, active=active, limit=limit, offset=offset)


@customers_router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: uuid.UUID, tenant: DataManager, db: DB):
    return svc.get_customer(db, customer_id)


@customers_router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: uuid.UUID, body: CustomerPatch, tenant: DataManager, db: DB, meta: Meta
):
    return svc.update_customer(db, tenant, customer_id, body, meta)


@customers_router.delete("/{customer_id}", status_code=NoContent)
def delete_customer(customer_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    """Only a customer with no sales can be deleted; otherwise archive (is_active false)."""
    svc.delete_customer(db, tenant, customer_id, meta)
    return Response(status_code=NoContent)


# --- suppliers ---------------------------------------------------------------------------------


@suppliers_router.post("", status_code=status.HTTP_201_CREATED, response_model=SupplierOut)
def create_supplier(body: SupplierCreate, tenant: DataManager, db: DB, meta: Meta):
    return svc.create_supplier(db, tenant, body, meta)


@suppliers_router.get("", response_model=SuppliersPage)
def list_suppliers(
    tenant: DataManager,
    db: DB,
    q: Search = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return svc.list_suppliers(db, q=q, active=active, limit=limit, offset=offset)


@suppliers_router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: uuid.UUID, tenant: DataManager, db: DB):
    return svc.get_supplier(db, supplier_id)


@suppliers_router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(
    supplier_id: uuid.UUID, body: SupplierPatch, tenant: DataManager, db: DB, meta: Meta
):
    return svc.update_supplier(db, tenant, supplier_id, body, meta)


@suppliers_router.delete("/{supplier_id}", status_code=NoContent)
def delete_supplier(supplier_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    svc.delete_supplier(db, tenant, supplier_id, meta)
    return Response(status_code=NoContent)


# --- products ----------------------------------------------------------------------------------


@products_router.post("", status_code=status.HTTP_201_CREATED, response_model=ProductOut)
def create_product(body: ProductCreate, tenant: DataManager, db: DB, meta: Meta):
    return svc.create_product(db, tenant, body, meta)


@products_router.get("", response_model=ProductsPage)
def list_products(
    tenant: DataManager,
    db: DB,
    q: Search = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return svc.list_products(db, q=q, active=active, limit=limit, offset=offset)


@products_router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, tenant: DataManager, db: DB):
    return svc.get_product(db, product_id)


@products_router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: uuid.UUID, body: ProductPatch, tenant: DataManager, db: DB, meta: Meta
):
    return svc.update_product(db, tenant, product_id, body, meta)


@products_router.delete("/{product_id}", status_code=NoContent)
def delete_product(product_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    svc.delete_product(db, tenant, product_id, meta)
    return Response(status_code=NoContent)


@products_router.get("/{product_id}/stock", response_model=StockLevelOut)
def stock_level(product_id: uuid.UUID, tenant: DataManager, db: DB, as_of: date | None = None):
    """Stock on hand at the end of a day (default: today in the UK): the sum of movements."""
    return svc.stock_level(db, product_id, as_of or today_uk())


# --- stock movements ---------------------------------------------------------------------------


@stock_router.post("", status_code=status.HTTP_201_CREATED, response_model=StockMovementOut)
def create_stock_movement(body: StockMovementCreate, tenant: DataManager, db: DB, meta: Meta):
    return svc.create_stock_movement(db, tenant, body, meta)


@stock_router.get("", response_model=StockPage)
def list_stock_movements(
    tenant: DataManager,
    db: DB,
    product_id: uuid.UUID | None = None,
    kind: Annotated[str | None, Query(pattern=f"^({'|'.join(STOCK_MOVEMENT_KINDS)})$")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return svc.list_stock_movements(
        db,
        product_id=product_id,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@stock_router.delete("/{movement_id}", status_code=NoContent)
def delete_stock_movement(movement_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    svc.delete_stock_movement(db, tenant, movement_id, meta)
    return Response(status_code=NoContent)
