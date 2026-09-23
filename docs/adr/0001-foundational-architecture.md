# ADR 0001 — Foundational architecture (Phase 0)

- **Status:** Accepted (2026-09-23)
- **Context:** `VYTERLIX_DEV_BREAKDOWN.md`, `VYTERLIX_IMPLEMENTATION_CHECKLIST.md` Phase 0

## 1. Service boundaries: modular monolith first

One FastAPI application, one PostgreSQL database, one Celery worker pool (added
in Phase 11). The 12 product modules + 5 supporting areas live as Python
packages inside `backend/app/` and talk to each other through service
functions, never through each other's tables directly.

Rationale: the intelligence loop (KPI → Health → Diagnosis → Forecast →
Recommendation → Action → Outcome → Memory) is tightly coupled by data. Splitting
it into network services before the vertical slice is proven would add
deployment and consistency cost with no benefit. Forecasting/ML stays in-process
(Python), and can be lifted into a separate worker later because it only reads
KPI values and writes forecasts.

## 2. Multi-tenant isolation: shared schema, row-level `organization_id`

- Every tenant-owned table carries a non-null `organization_id` FK to
  `organizations`, indexed (usually as the leading column of composite indexes).
- **Enforcement layer 1 (Phase 2):** all tenant data access goes through a
  tenant-scoped repository/session helper that injects
  `WHERE organization_id = :current_org`. Route handlers never build raw
  unscoped queries against tenant tables.
- **Enforcement layer 2 (Phase 25 hardening, designed for now):** PostgreSQL
  Row-Level Security policies keyed off `current_setting('app.current_org')`,
  so a missed filter in application code fails closed.

Schema-per-tenant was rejected: migrations fan out per tenant, cross-tenant
admin/reporting gets harder, and SME tenant counts will be high with small
per-tenant volume.

## 3. Primary keys

UUID (v4) primary keys on all tables. Avoids enumerable IDs leaking tenant
volume and lets records be created client-side/offline (mobile) if needed.

## 4. Canonical normalized transaction model

All analytics read from normalized tables, never from source-specific shapes.
Each connector (CSV, Xero, Shopify, WooCommerce, GA) maps into:

| Field | Notes |
|---|---|
| `organization_id` | tenant |
| `occurred_on` | transaction date (business's local date) |
| `customer_id` | nullable (walk-in / anonymous) |
| `product_id` | nullable for non-product revenue |
| `quantity`, `unit_price`, `discount`, `tax` | `NUMERIC`, never float |
| `cost` | COGS for the line, nullable if unknown (tracked as data-quality gap) |
| `revenue` | net line revenue after discount, excluding tax |
| `currency` | ISO 4217; multi-currency-ready, GBP at launch |
| `channel` | normalized enum-ish code (online, in_store, marketplace, …) |
| `source`, `source_ref` | connector + external ID, used for duplicate detection |

Physically this is `sales` + `sale_items` (header/line); `Transaction` is the
logical view analytics code uses.

## 5. Money and numbers

`NUMERIC(18,4)` for money in the DB, `decimal.Decimal` in Python. Floats are
only permitted inside statistical/ML code, and results are rounded back to
`Decimal` at the storage boundary.

## 6. Layer separation (standing rule)

Deterministic business logic → statistical/ML services → recommendation rules
→ business memory → LLM (narration only). Every score, diagnosis, forecast and
recommendation row stores `rule_version`/`model_version` and references to its
evidence.
