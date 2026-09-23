"""Tenant isolation at the data-access layer (ADR 0001 §2, layer 1).

Every model using TenantScopedMixin holds one business's data. Two rules, enforced for
every ORM query on a Session:

1. Scoped: when a tenant is set on the session (see `tenant_scope`), every SELECT,
   UPDATE and DELETE touching a tenant table automatically gets
   `organization_id = <tenant>`, including joins, subqueries and relationship loads.
   New tenant rows get that organization_id; rows for another tenant are refused.

2. Fail closed: with NO tenant set, touching a tenant table raises TenantScopeError
   instead of silently reading every business's data. Code that genuinely works across
   tenants (e.g. "which businesses do I belong to?") must opt out explicitly with
   `.execution_options(**ACROSS_TENANTS)`, which makes those places easy to audit.

Layer 2 (PostgreSQL row-level security) comes in hardening phase L1.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql.util import find_tables

from app.db.base import Base, TenantScopedMixin

_TENANT_KEY = "tenant_id"
_SKIP_OPTION = "vyterlix_across_tenants"
ACROSS_TENANTS = {_SKIP_OPTION: True}


class TenantScopeError(RuntimeError):
    """A tenant table was used without a tenant: a bug, never a user error."""


@cache
def _tenant_tables() -> frozenset[sa.Table]:
    return frozenset(
        m.local_table for m in Base.registry.mappers if issubclass(m.class_, TenantScopedMixin)
    )


def current_tenant(session: Session) -> uuid.UUID | None:
    return session.info.get(_TENANT_KEY)


@contextmanager
def tenant_scope(session: Session, organization_id: uuid.UUID) -> Iterator[None]:
    """Scope every query on `session` to one organisation for the duration of the block."""
    previous = session.info.get(_TENANT_KEY)
    if previous is not None and previous != organization_id:
        raise TenantScopeError("Session is already scoped to a different organisation")
    session.info[_TENANT_KEY] = organization_id
    try:
        yield
    finally:
        if previous is None:
            session.info.pop(_TENANT_KEY, None)


@event.listens_for(Session, "do_orm_execute")
def _scope_queries(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(_SKIP_OPTION, False):
        return

    tenant_id = state.session.info.get(_TENANT_KEY)
    if tenant_id is None:
        if state.is_relationship_load:
            return  # loading children of an object that was itself fetched legitimately
        touched = find_tables(state.statement, include_crud=True, check_columns=True)
        if _tenant_tables().intersection(touched):
            raise TenantScopeError(
                "Query touches business data without an organisation in scope. Use "
                "tenant_scope(), or ACROSS_TENANTS if it deliberately spans organisations."
            )
        return

    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScopedMixin,
            lambda cls: cls.organization_id == tenant_id,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_new_rows(session: Session, flush_context, instances) -> None:
    tenant_id = session.info.get(_TENANT_KEY)
    if tenant_id is None:
        return
    for obj in session.new:
        if not isinstance(obj, TenantScopedMixin):
            continue
        if obj.organization_id is None:
            obj.organization_id = tenant_id
        elif obj.organization_id != tenant_id:
            raise TenantScopeError("Refusing to write a row for a different organisation")
