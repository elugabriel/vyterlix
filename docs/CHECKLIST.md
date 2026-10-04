# Vyterlix — Build Checklist

The single tracker for the whole build. Tick items **in the same commit as the work**, so
this file always matches `main`.

`[x]` done · `[ ]` to do · `[~]` partly done (see note) · **→** moved elsewhere (see where)

Sources: `VYTERLIX_IMPLEMENTATION_CHECKLIST.md` (build order),
`Vyterlix_Master_Implementation_Checklist.md` (launch phases), PRD and brief.

## Progress

| Phase | Name | Status |
|---|---|---|
| −1 | Decisions before code | 20 / 23 decided |
| 0 | Architecture + database design | In progress |
| 1 | Project foundation | Done |
| 2 | Authentication + multi-tenancy | Done |
| 3 | Business onboarding & profile | Done |
| 4 | Data import + normalisation | **Part A, milestone, job queue (10) and integration framework (11) done; real connectors (Xero, Shopify, WooCommerce, Google Analytics) remain** |
| 5 | KPI engine | **Done apart from marketing (needs Google Analytics) and cash/runway (needs Xero): 28 KPIs, breakdowns, Key figures page** |
| 6 | Business health engine | **Step 1 and 2 done (scoring, rules as data, explanations, history, page; seasonal "usual"); real sector benchmarks wait for real sources** |
| 7 | Diagnostic engine | **Done apart from the parked items: anomalies, segments (product, channel, customer, day, cost), drivers, evidence and diagnoses with confidence, and the What changed page with the explanation screen** |
| 8 | Forecasting engine | **Steps 1-2 done (swappable methods, sales forecast with a range on every month, accuracy tracking, the forecast page); the other forecasts next** |
| 9 | Recommendation engine | Not started |
| 10 | Action management | Not started |
| 11 | Follow-up + outcome measurement | Not started |
| ⚑ | Vertical slice proof | Not started |
| 12 | Business memory / learning | Not started |
| 13 | AI consultation | Not started |
| 14 | Alerts + notifications | Not started |
| — | Core UX screens | Not started |
| 15 | Reporting | Not started |
| 16 | Subscription / billing | Not started |
| 17 | Admin portal | Not started |
| 18 | Mobile apps (iOS + Android, Flutter) | **Confirmed in scope** — not started |
| L1 | Security & privacy hardening | Not started |
| L2 | Automated QA & testing | Ongoing |
| L3 | Production infrastructure | Not started |
| L4 | Production deployment | Not started |
| L5 | App store release | Not started |
| L6 | UAT & go-live | Not started |
| L7 | Post-launch operations | Not started |

---

## Standing rule: UK-first (confirmed 2026-09-26)

Everything defaults to the UK, in every phase:
- **Currency:** GBP (£) only at launch. The `currency` column stays (multi-currency-ready schema) but only GBP is accepted.
- **Location:** country GB; UK nations/regions (England's regions, Scotland, Wales, Northern Ireland), UK towns/cities, UK postcodes (format-validated).
- **Time and format:** Europe/London time zone (BST handled), dates dd/mm/yyyy, weeks start Monday, £1,234.56 numbers, +44 phone numbers.
- **Language:** British English in the UI and emails (organisation, colour, analyse…).
- **Tax and classification:** UK VAT rates (20% / 5% / 0%), UK SIC 2007 codes, financial-year start configurable (common: 1 April; the UK tax year starts 6 April).
- **Data:** seed lists, sample datasets and benchmarks use UK examples.

## Phase −1: Decisions before code

- [x] Target company profile — SME, sector-agnostic at MVP
- [x] SME-only vs enterprise — SME-primary; 4 pricing tiers exist from MVP; Corporate features Phase 2+
- [x] Geography & currency — UK launch, UK GDPR; multi-currency-ready schema, GBP first
- [ ] **What data the first real customer(s) will provide; sample datasets** — needs Dolaris
- [x] Platforms customers use — Xero, QuickBooks, Shopify, WooCommerce, GA, Meta, social, POS, CSV
- [x] MVP integrations — Xero, Shopify, WooCommerce, Google Analytics; CSV/Excel + manual always
- [x] MVP KPI set — financial, sales, customer, marketing, inventory (see implementation checklist)
- [x] Health scoring approach — per-business personalised, never a generic score
- [x] MVP forecasts — revenue, cash flow, customer demand, inventory, churn/retention
- [x] Who approves recommendations — Owner (all) + Manager (within remit); Viewer read-only
- [x] LLM provider — Anthropic Claude behind a provider-agnostic wrapper
- [x] Data to external AI — only with documented purpose + controls; subprocessors disclosed
- [x] Customer data for model improvement — per-organisation opt-in, default OFF (legal review before launch)
- [x] Explainability — every figure traceable to data + rule/model version
- [x] "Personalised consultation" — Business Memory (conversations + interventions)
- [x] Role differences — Owner / Manager / Viewer at MVP
- [x] Notification channels — in-app, email, mobile push
- [x] "Critical" alerts — only security messages are un-suppressible
- [ ] **Launch tiers & feature gating** — £99 / £199 / £299 / Corporate as placeholders; confirm before launch
- [x] Payment gateway — Stripe primary, Paystack secondary, pluggable per organisation
- [x] **Mobile apps will be built** (decided 2026-10-01): iOS and Android, alongside the web app, on the one shared backend. Required by the SLA (clause 3.2) and proposal
- [x] **Mobile technology: Flutter** (decided 2026-10-01), as in the proposal: Dart, one codebase for iOS and Android. This is the only part of the project not written in Python (the backend and web app stay Python / plain HTML-JS; still no Node). iOS builds need a Mac with Xcode (or a cloud macOS build service) and an Apple Developer account: plan this under L5
- [ ] **Mobile scope and timing in writing** — SLA says native iOS + Android; proposal's mid-project milestone is "feature-complete Web/Android/iOS apps ready for UAT"; the SLA timetable puts different work in Week 3. Agree the exact meaning with Dolaris and sign it (SLA clause 12.1) — it also decides when the 30% milestone payment falls due

## Phase 0: Architecture + database design

- [~] Module decomposition (12 modules + 5 supporting areas) — modular monolith decided (ADR 0001); per-module boundaries to be written as each module starts
- [ ] ERD covering groups A–M (only A, identity/tenancy, is built so far)
- [ ] Confirm central relationship: Organization → Metrics, Alerts, Diagnoses, Forecasts, Recommendations → Actions → Outcomes, Memory
- [x] Multi-tenant isolation — shared schema, `organization_id` on every tenant table (ADR 0001 §2)
- [x] Repo structure & service boundaries — monolith-first (ADR 0001 §1)
- [x] Canonical normalised transaction model (ADR 0001 §4)
- [x] Money as `NUMERIC` / `Decimal`, UUID primary keys (ADR 0001 §3, §5)

## Phase 1: Project foundation

- [x] Repository, branch strategy (main + one branch per step), CI (lint, format, tests)
- [x] FastAPI project structure
- [x] Static frontend structure (plain HTML/CSS/JS, `css/`, `js/`, `js/pages/`)
- [x] CORS allow-list; wildcard refused in staging/prod
- [x] Local static server documented (`python frontend/serve.py`: no-cache dev server, since Phase 3)
- [~] PostgreSQL — dev + test done → staging/prod moved to **L3**
- [x] Per-environment config (`VYTERLIX_*`, `.env`)
- [x] Frontend `config.js` for API base URL
- [x] Migrations (Alembic)
- [x] Structured JSON logging with request IDs
- [x] API versioning (`/api/v1`)
- [x] Centralised error handling (one error envelope)
- [~] Test framework + CI gate — backend pytest done; frontend JS unit tests dropped (no Node) → covered by **L2** browser/E2E tests
- [x] README

## Phase 2: Authentication + multi-tenancy

**Done when:** a user can register, log in, create a business and invite teammates, and
no user can ever see another business's data.

Decisions: Owner/Manager/Viewer · access token in memory + httpOnly refresh cookie ·
Argon2id · dev emails to log · **forgot-password always included** · unverified users log in with limited access.

- [x] 1. Tables + migration: users, organizations, organization_users, roles, permissions, role_permissions, user_sessions, user_tokens, audit_logs; roles/permissions seeded
- [x] 2. Registration — `POST /auth/register`
- [x] 3. Email verification — `POST /auth/verify-email`, `POST /auth/resend-verification`
- [x] 4. Login / logout / refresh — `POST /auth/login`, `/auth/refresh`, `/auth/logout`, `GET /me`; failed-login rate limiting; unverified users may log in with limited access (`VerifiedUser` dependency gates everything else)
- [x] 5. **Forgot password / reset password** — `POST /auth/forgot-password`, `/auth/reset-password`; no email enumeration; 1-hour single-use link; all sessions revoked; lockout lifted; "password changed" alert email
- [x] 6. User profile — `GET`/`PATCH /me` (name), `POST /me/change-password` (needs current password; keeps this device, logs out others; cancels open reset links; counts toward login lockout; alert email)
- [x] 7. Organisation creation — `POST /organizations` (verified users only; creator becomes Owner), `GET /organizations` (mine, with my role), `GET /organizations/{id}` (non-members get 404)
- [x] 8. Tenant isolation at data-access layer + cross-tenant tests — `app/db/tenant.py` (automatic scoping + fail-closed guard), `CurrentTenant` dependency, `GET /organizations/{id}/members`; mutation-tested
- [x] 9. Role/permission enforcement — `require_permission(Perm.X)` (DB-driven), `PATCH /organizations/{id}` (org.manage), `PATCH /organizations/{id}/members/{user_id}` role + remit (members.manage), never-lose-the-last-owner rule, Manager remit by KPI category (`tenant.can(perm, category)`)
- [x] 10. Invitations — invite by email + role/remit (owner), list with status, revoke, preview, accept (logged-in account email must match; accepting verifies the email; 7-day single-use link; re-invite replaces old link)
- [x] 11. Audit logging — all 18 sensitive actions audited via `AuditAction` (gaps closed: blocked logins, disabled-account logins, wrong-account invitation use); append-only enforced by a DB trigger; real write times (`clock_timestamp()`); `GET /organizations/{id}/audit-log` for owners (new `audit.view` permission)
- [x] 12. Frontend pages: register, login, verify email, forgot/reset password, create business (`app.html`), accept invite; shared `js/auth.js` (in-memory access token, refresh-cookie session restore, auto-refresh), `js/ui.js`; safe `?next=` redirects; tokens stripped from URLs; static safety checks in `tests/test_frontend_static.py`; full journey tested in a real browser

## Phase 3: Business onboarding & profile

**Done when:** a user can set up a full business profile, goals and preferences.

Decisions (confirmed 2026-09-26):
- **Industry:** a short Vyterlix list (code + label, stored as data so it can grow) plus an
  optional UK SIC 2007 code for precision later.
- **Required before the dashboard:** business name, industry, currency, financial-year start.
  Everything else can be skipped and completed later (onboarding progress shows what's left).
- **Seasonality:** entered by the user now; once sales data exists (Phase 4+), Vyterlix also
  *detects* seasonality and *suggests* it for the user to confirm (never applied silently).

- [x] 1. Tables + migration: `industries` (14 seeded), business_profiles, business_settings, business_goals, business_seasons, business_benchmarks — UK-first rules enforced as database constraints (GBP, GB, UK postcode/region/SIC/VAT formats, Europe/London, en-GB, real financial-year start dates); business tables automatically tenant-scoped
- [x] 2. Business profile API — `GET /industries`; `GET/PUT/PATCH /organizations/{id}/profile` (read: any member; change: org.manage); UK input tidying in `app/core/uk.py` (postcodes, GB VAT numbers, UK dates); years operating derived from year founded; audited
- [x] 3. Business goals — `GET/POST /organizations/{id}/goals`, `GET/PATCH …/goals/{goal_id}` (close via status achieved/abandoned); new `goals.manage` permission (owner + manager); each goal type maps to a KPI area and Managers are held to their remit; exact £ (pence) / % / count targets; target dates not in the past (UK date); audited
- [x] 4. Business lists — offerings (products/services), sales channels, customer types, cost categories in one `business_list_items` table; `GET /business-list-suggestions` (UK starting points); `GET/POST /organizations/{id}/lists/{kind}`, `…/bulk`, `PATCH …/{item_id}`; names unique per list ignoring capitals (DB index); archive instead of delete; cost categories flag "cost of sales" for gross profit
- [x] 5. Seasonality — `GET/POST /organizations/{id}/seasons`, `PATCH …/{season_id}` (edit, confirm, dismiss, restore), `GET …/seasons/on?date=` (default today, UK); yearly periods that may cross New Year; UK labels ("1 Dec – 5 Jan"); detected seasons arrive as suggestions and never apply until confirmed; `seasons_on()` ready for alerts
- [x] 6. Business settings + notification preferences — `GET/PATCH /organizations/{id}/settings` (UK defaults; week start + quiet hours editable by owners; time zone/locale/currency fixed); `GET/PATCH …/notification-preferences` (each member's own, per business; email / in-app / push per alert type; defaults all on; security email + in-app can't be switched off, enforced in the database too)
- [x] 7. Benchmark storage — staff CSV loader `python -m app.cli.benchmarks load` (all-or-nothing, row-numbered errors, dry run, re-load updates, source required, UK regions only); `GET /benchmarks` (verified users, read-only); `GET /organizations/{id}/benchmarks` closest match per KPI (SIC → region → size → whole UK) with `matched_on`; empty until real UK sources are chosen
- [x] 8. Onboarding progress — `GET /organizations/{id}/onboarding` (9 sections, status worked out from real data, counts, `next_section`, `ready_for_dashboard`); `POST …/skip` (optional sections only) and `POST …/complete` (needs the required section; audited once)
- [x] 9. Frontend — `onboarding.html` (9-step wizard: progress bar, step list with status, save and continue, skip, finish; reused as the editor for each section), `business.html` (setup checklist with edit links, business settings, my notifications grid with security locked), businesses list links + create-then-onboard; UK formatting (£, dd/mm/yyyy, UK regions); `frontend/serve.py` no-cache dev server; full journey tested in a real browser

## Phase 4: Data import + normalisation

Decisions (confirmed 2026-09-28):
- **File storage:** dev = a local folder outside the repo; production = UK-hosted storage (chosen in L3).
- **Original files:** kept 90 days, then deleted automatically (UK GDPR minimisation); imported records stay.
- **Limits:** 25 MB and 250,000 rows per file.
- **Rows with errors:** always preview first; then import the valid rows, skip the rest, and offer a downloadable list of problems.
- **VAT:** each import asks whether amounts include VAT (default yes for consumer sales); revenue is stored **excluding** VAT, with VAT kept alongside (ADR 0001 §4).
- **Excel:** modern `.xlsx` only; choose the sheet when there's more than one.
- **Sample data:** no real customer files yet, so an obviously fake UK retail dataset is created for testing and the milestone (clearly labelled, never mixed with real data).
- **Part B (connectors), decide later:** background jobs via a PostgreSQL-backed queue (recommended: no Redis/Docker needed); Dolaris registers developer apps with Xero, Shopify and Google.


- [x] Trading data tables (step 1): customers, suppliers, products, sales, sale_lines, expenses, stock_movements — tenant-scoped; same-business links enforced by composite (organization_id, id) foreign keys; GBP only, net + VAT = gross exactly, UK VAT rates, refunds/credits as negative kinds, stock direction by kind; source + source_ref stop double imports; unknown cost of goods allowed (reported later, never guessed)
- [x] Import tracking tables (step 2): data_sources, data_imports, data_import_rows, data_quality_issues + file storage (records carry `import_id` for undo; local storage outside the repo; `python -m app.cli.uploads purge` deletes originals after 90 days and clears raw row copies)
- [x] CSV upload (step 3): `POST /organizations/{id}/imports` (multipart; needs `data.manage`); UTF-8 or Windows-1252, comma/semicolon/tab/pipe, headings on any row 1-100; preview (headings, 20 sample rows, row count); same-file warning; 25 MB / 250,000 row limits refused before reading; refused uploads leave no file or record; `GET` list/one, `PATCH` sheet/header row
- [x] Excel upload (step 3): `.xlsx` only; sheet chooser for multi-sheet workbooks (hidden sheets skipped); old `.xls`, macro and password-protected workbooks refused with advice; zip-bomb and wrong-size-workbook protection
- [x] Column-mapping UI and the data screens (step 9; browser-tested): Data overview (score, what to fix first, per-dataset and month-by-month, origins), Upload a file (sheet chooser, preview, column matching with VAT questions, check, problem rows and CSV download, import, undo), Import history, and Type in data (sale with lines, expense, customer, supplier, product, stock) with plain-English errors; fake UK files in `sample-data/` (backend done in step 4: `GET/PUT /organizations/{id}/imports/{id}/mapping`, `GET /organizations/{id}/data-sources`; suggestions from a saved mapping or well-known column names, never guessed loosely; every import must answer "do amounts include VAT?" and, with no VAT column, which UK rate; mappings can be saved by name and are offered for the next similar file; the page itself is step 9)
- [x] Validation on import (step 5): `POST .../imports/{id}/validate` checks every row against the saved mapping: UK dates (day first; US-style and impossible dates refused), pounds-only amounts (`£1,234.50`, `(12.50)`, `-12.50`; a comma used for pence is refused, never read as 450), UK VAT rates only, VAT split to the penny so net + VAT = gross, refunds/credits as negatives, emails, UK postcodes, stock-movement direction; all problems in a row reported together; nothing is imported by checking. Results kept per row (only mapped columns, so unrelated personal data in the file isn't copied); summary with totals, date range and warnings (no reference column, no cost of goods, mapping looks wrong)
- [x] Duplicate detection (step 5): by order/invoice/receipt number within the file and against what is already stored from the same source; customers by email, suppliers by name, products by code; an identical repeated row is a duplicate, the same reference with different details is a problem to settle; rows with no reference are never treated as repeats
- [x] Error reporting on failed rows (step 5): `GET .../rows?status=invalid|duplicate` (paged), `GET .../problems.csv` download (Excel-safe, formula-injection protected, column order as on the mapping screen)
- [x] Import history (step 6): `GET .../imports` newest first with who uploaded, status, counts and times; filter by status or kind of file, paged; `GET .../imports/{id}/records` shows what an import currently has in the data; undone imports stay in the history
- [x] Import and undo (step 6): `POST .../imports/{id}/import` creates the records from the valid rows (all or nothing; one transaction; locked so it can't run twice); sales also get a line when the row says what was sold; customers, products, suppliers, sales channels and cost categories are found by email/code/name or created once; anything added since checking that is already there is skipped. `POST .../imports/{id}/undo` removes everything the import created in dependency order (all or nothing); refused with a clear message if later data depends on it (undo the later import first); sales channels and cost categories are the business's own lists and stay. Measured on the real server: 50,000 rows import in about 60 s and undo in about 6 s
- [x] Manual data entry (step 7): the system works with no files and no integrations. Owners and Managers (`data.manage`) can create, read, correct and delete, by hand: sales (with optional line detail that must add up), expenses, customers, suppliers, products and stock movements; `GET .../products/{id}/stock` gives stock on hand for any day. Amounts are entered positive with a kind (sale/refund, expense/credit), the VAT question is always answered (rate or amount, includes VAT or not), UK VAT rates only, money returned in pounds and pence. Customers/suppliers/products in use are archived, not deleted; every write is audited without personal details; each write is all-or-nothing. The VAT arithmetic is shared with the file import (`services/money.py`) so the two always agree. Records that came from a file can be corrected too and keep their source
- [x] Normalisation onto the canonical transaction model (step 6): imported rows become sales, sale lines, expenses, customers, suppliers, products and stock movements in GBP, net + VAT = gross, with the import that made them and where they came from
- [x] Data-quality scoring per source/period (step 8): `GET /organizations/{id}/data-quality` gives an overall score (0-100, good/fair/poor), a score per dataset (sales, expenses, customers, products, stock) built from named, weighted checks that each explain themselves with the real figures (no missing months, up to date, cost of goods known, says what was sold, costs categorised, duplicates, negative stock), a month-by-month table (24 months), how each recent import went, where records came from, and a ranked list of what to fix with a plain-English instruction for each. Sales count 3x, expenses 2x; no expenses counts as zero because profit can't be worked out. Viewers can read it; `POST .../data-quality/refresh` (Owner/Manager) saves the problems (new added, fixed ones marked resolved, continuing ones updated) for alerts and the health score; `GET .../data-quality/issues` lists them. Scoring rules are pure and unit-tested; no data is guessed
- [x] Background job queue (step 10): PostgreSQL is the queue (no Redis or Docker). `jobs` table (tenant-scoped; one active job per upload; attempts, retry delay, heartbeat, progress, result); `python -m app.cli.worker run|status|prune` claims jobs with `FOR UPDATE SKIP LOCKED` so several workers can run side by side; an expected failure fails at once with the same plain-English message, a crash retries (3 tries, growing delay), a worker that dies is noticed by its stale heartbeat and the job is re-queued; the requester's permission is re-checked when the job runs. `POST .../imports/{id}/jobs` (`validate`, `import`, `undo`) returns 202; `GET .../imports/{id}/jobs` and `GET .../jobs/{id}` give status, percent and result. Files over 5,000 rows are refused by the direct validate/import endpoints. The upload page now uses jobs for check, import and undo, with a live progress bar and picks a running job up again after a reload. Browser-tested with 12,000 rows
- [x] Integration framework (step 11): the shared plumbing every connector uses. One small `Provider` class per system (`app/integrations/base.py`: authorise address, swap code for tokens, refresh, which account, revoke, sync); OAuth 2.0 with PKCE and a single-use, 10-minute, hashed `state` bound to the person and business; tokens and keys stored **encrypted** (Fernet, key from `VYTERLIX_ENCRYPTION_KEY`, rotation supported, the public dev key refused outside dev) and never sent to the browser; tokens refreshed before they expire (row-locked so two workers don't both refresh); sync runs as a background job and every sync leaves a row (counts, outcome, error, the job, and the import it produced = provenance); a provider that refuses our tokens puts the connection in 'needs sign-in again' with a plain message (no useless retries), a temporary outage is retried and shows as 'needs attention', and re-connecting must be the same account; disconnect wipes the tokens (and tells the provider if it can) but keeps history and imported data; new `integrations.manage` permission (Owner) for connect/disconnect, `data.manage` to view and update; audit log entries for connect, disconnect and sign-in-needed. API: `GET .../integrations/providers`, `GET .../integrations`, `POST .../connect`, `POST .../callback`, `POST .../{id}/sync` (job), `GET .../{id}/syncs`, `POST .../{id}/disconnect`. Connections page shows what will be read BEFORE connecting. A dev-only 'Sandbox' provider lets the whole flow be tried with no real account. Real connectors (Xero, Shopify, WooCommerce, Google Analytics) come next and each needs its provider developer app registered by Dolaris. Not yet built: scheduled (automatic) syncs, fetched records becoming sales/expenses (each connector does this), key-rotation CLI
- [ ] Connector: **Xero** (accounting)
- [ ] Connector: **Shopify**
- [ ] Connector: **WooCommerce**
- [ ] Connector: **Google Analytics**

- [x] **Phase 4 milestone (done)**: a whole fake UK business year goes through the real pipeline and exactly the expected totals come out. `app/demo/milestone.py` invents a small bakery (1 Oct 2025 to 30 Sep 2026: 15,864 sales, 103 expenses, 400 customers, 12 products, 1,489 stock movements; about £102k sales, a thin profit, a Christmas peak and a January dip, a few refunds, no gaps) and works out every total as it goes; `tests/test_milestone_dataset.py` uploads, matches, checks and imports all five files through the real API and the background worker and compares sales, VAT, cost of goods, expenses (per month), customers, stock on hand and the data-quality score (100) to the penny. `python -m app.cli.demo load|clear|files` puts the year into (or takes it out of) a business whose name ends "(demo data)" (31 s); it refuses any other business.

**Done when:** Sales/Expenses/Customers/Inventory CSVs become normalised records.

## Phase 5: KPI / business intelligence engine

Design (Phase 5 step 1): small SQL **measures** in code (`app/kpi/measures.py`: revenue, cogs, operating expenses, sales count...), and **KPI definitions as data** (`kpi_definitions`: name, plain-English meaning, unit, formula such as `(revenue - cogs) / revenue * 100`, which way is better, which records it needs). Formulas are parsed with a strict whitelist (`app/kpi/expression.py`: numbers, measure names, + - * /, `prev()` and `yoy()`; nothing else can run). A new KPI that recombines existing measures is a new row, not a code change. Conventions are written at the top of `measures.py`: money is net of VAT; refunds are negative sales and give their cost back; running costs exclude categories marked "cost of sales" (the stock bought is already in cost of goods sold, so profit is not counted twice).

- [x] Tables: kpi_definitions (global, 15 seeded), kpi_values (per business, period, with previous value, change, completeness, data quality and the inputs it came from), kpi_calculation_runs (who, when, how it went)
- [x] KPI engine as a service (`app/services/kpi.py`, not in route handlers); runs as the `kpi.calculate` background job, queued automatically after every import or undo, or by the owner (`POST /kpis/calculate`); a broken formula is skipped, not fatal; a KPI that cannot be worked out says so (`no_data` / `undefined`) instead of showing a made-up number
- [x] KPI definitions stored as data, not hard-coded
- [x] Financial: revenue, takings incl. VAT, cost of goods sold, gross profit and margin, running costs, net profit and margin, stock bought. **Not yet:** cash position and runway (need bank or accounting data: the Xero connector)
- [x] Sales: sales count, average sale, items sold, refund rate, growth on the previous period, comparison with the same period last year; **sales by channel and by product** (step 2: `GET .../kpis/breakdown/{channel|product}`, biggest first, the rest rolled into "Everything else", product gross profit)
- [x] Customer (step 2): customers who bought, new, returning, repeat rate, retention, churn, spend per customer, and how many sales name a customer (so you know how far to trust them). **Not yet:** CAC (needs marketing spend) and a true lifetime value (needs more history)
- [ ] Marketing: spend, conversion rate, ROAS/CAC by channel, traffic (needs the Google Analytics connector and ad spend data)
- [x] Inventory (step 2): items in stock, stock value at cost, stock turnover, days of stock, products out of stock, cost of goods sold; fast movers, slow movers and dead stock (`GET .../kpis/stock/movers`)
- [x] KPI history storage (every period kept; history endpoint, 24 periods by default)
- [x] Period-over-period comparison (previous period and change on every value; weeks, months, quarters and years supported by the engine; the page uses months)
- [x] Key figures page (`kpis.html`): everyone can look; the owner can recalculate; shows change since last month, data quality warnings, a chart and the figures behind each number. Verified against the demo year to the penny

## Phase 6: Business health engine

Design (step 1): the score is built from the KPIs the KPI engine stored, judged by **rules held as data** (`health_rules`: which KPI, which area, how much it counts, whether higher or lower is better, and three anchors: bad = 0, ok = 60, good = 100, straight lines between). A rule judges either the KPI's own value (a margin of 12%) or how far it is from **this business's own usual** (the average of its earlier 6 months; needs at least 3), so no outside benchmark is needed to start. Metrics roll up into areas and areas into one score, each as a weighted average; areas with no data (marketing today) are left out and the score says how much of the picture it covers (and gives no overall score below 40%). Every area stores a plain-English explanation and the exact metrics behind it. Only finished months are scored. Starting thresholds are labelled as Vyterlix's own choices, to be replaced by real sector benchmarks when loaded.

- [x] Tables: business_health, business_health_components, health_rules (plus health_category_weights)
- [x] Areas: Money, Sales, Customers, Stock scored now; Marketing (needs Google Analytics) and Operations (needs the actions engine) counted once they have data
- [x] Per-business baseline ("normal" for this business): the average of the earlier six months
- [x] Configurable health rules and area weights, with per-industry overrides (a rule or weight written for an industry replaces the general one for businesses in it). No industry-specific rules are seeded yet: that needs real benchmarks
- [x] Weighted overall score, status (healthy 80+, fair 60+, needs attention 40+, at risk), coverage and the lowest data quality among the figures used
- [x] Trend per area and overall (up / down / flat, against the month straight before; a 3-point move counts)
- [x] Explanation stored for every area (click-through to evidence: each metric's value, usual level, how far from it, score and a sentence)
- [x] Health history (every finished month, recalculated whenever the KPIs are: `GET .../business-health`, `/history`, `/{month}`)
- [x] Business health page (`health.html`): headline score and why, a card per area that opens to its figures, a month picker, and a month-by-month chart

Design (step 2): health rules marked **seasonal** (sales, profit and active customers; a flag in the rule, so it is data) are judged against a better "usual" than the plain six-month average. In order: **the same month last year** when the business has it (it already contains the season); otherwise the six-month average **adjusted for the busy and quiet seasons the owner has confirmed** (each earlier month is stripped of its own season, averaged, then put into this month's season; a season covering half a month counts for half of it, and where seasons overlap the stronger one counts for that day); otherwise the plain average as before. Seasons that were only suggested, or have no expected change, are never used. Margins, order size and stock turnover don't follow the trading year and are unchanged. Each figure's sentence says which yardstick was used.

- [x] Seasonal flag on health rules (`health_rules.seasonal`, migration `9b3e4d7a2c18`)
- [x] Same month last year as "usual" once there is a year of history
- [x] Owner's confirmed seasons adjust the average when there is not yet a year
- [x] Evidence says how "usual" was worked out (`baseline_kind`: average / last year / seasonal) in the figure's sentence and the API

## Phase 7: Diagnostic / explanation engine

Design (step 1, then step 2 for anomalies): the engine first **notices** that something moved (this step), then, in later steps, finds **why**. Noticing reads the monthly KPI values the KPI engine already stored (it never recalculates them) and saves a `detection_events` row for every finished month in which a figure moved by more than is normal. "More than normal" depends on the kind of figure: pounds, counts and ratios by per cent (15% noticeable, 30% big; nothing from a starting point under £100 or 5 items), figures that are already a per cent (margins) by percentage points (3 noticeable, 8 big). Each event says whether it is good or bad news (which way is better for that figure), carries the numbers and a plain sentence, and is marked as **expected** when the owner's confirmed busy and quiet seasons account for most of it. The rules are pure functions in `app/diagnostics/detection.py` (Vyterlix's own starting values, in one place); detection runs after every KPI calculation, best-effort like the health score.

- [x] Tables: detection_events, diagnoses, diagnostic_evidence (diagnoses and evidence are in place for the next steps; evidence is typed fact / statistical / AI interpretation / insufficient, so a guess is never shown as a fact)
- [x] Material-change detection (step 1: month against the month before; `GET /organizations/{id}/changes`, filter by month, good/bad, size, hide expected; `GET .../changes/{id}`)
- [x] "What changed" page (`changes.html`): newest month first, biggest first, good/bad, expected-for-the-time-of-year note, link to the figure
- [x] Anomaly detection (step 2): a month is flagged as unusual when a figure is 3 or more "normal wobbles" from the middle of its own last 12 finished months (needs 6), and is also a big enough change to matter (same size bands). Robust to one wild month (middle value, not average); a very steady figure is allowed a little wobble (2% of usual, half a point for a %). Seasonal figures are judged with each month's confirmed season taken out. Catches a slow slide that no single month shows. Same table and page as changes (`kind` = anomaly; `?kind=` filter); the page shows both on one card.
- [~] Segment analysis (step 3): `GET /organizations/{id}/segments` lists what can be split; `GET .../segments/{figure}/{split}?month=&against=previous_month|last_year&limit=` splits a figure for a month into its parts, each compared with the month before (or last year), biggest movers first, with the share of the overall change each part is (over 100% or negative when parts moved against each other) and a plain sentence. Sales by product, channel, customer and day of the week; number of sales by channel, customer and weekday; items sold and gross profit by product; running costs by cost category and supplier. The parts always add up to the Key figures number (a test checks every split against the KPI engine); sales recorded without product detail get a row of their own. Read from the records when asked, nothing stored. Shown on the What changed page ("Where did this come from?"). **Not yet:** location (customer postcode area), price and quantity effects, inventory, customer groups, other KPIs (margins, customers who came back)
- [~] Driver/contributor identification (step 4): `GET /organizations/{id}/drivers/{figure}?month=&against=` reads a change through lenses that each split the SAME change in two effects adding up to it exactly: **days** (a month with more days sells more, against busier or slower days), **orders** (more or fewer sales, against a bigger or smaller average sale) and **price and volume** (items sold, against the prices actually charged after discounts; sales recorded with no product detail named separately). Plus any single product, channel, customer, day, cost category or supplier that accounts for at least a quarter of the change. Findings are ranked by share of the change, each with a plain sentence; lenses are different readings of one change, so they are not added together. For sales, number of sales, items sold and gross profit (days lens) and running costs (parts only). Shown on the What changed page. **Not yet:** a cross-figure cause (sales fell AND refunds rose), weekday mix between months, customer mix, margins and customer figures
- [x] Evidence generation + storage (step 5): `POST /organizations/{id}/changes/{change}/diagnosis` (owner or manager) explains a detected change and keeps the explanation; `GET` reads it back (viewers too). Safe to run again (replaces the evidence, same diagnosis). Evidence: the figure itself and how complete its data is; the drivers (parts read off the records are facts, splits worked out by arithmetic are statistical); how far from usual an unusual month was; what the owner's seasons expect; and what the engine cannot tell. Marked on the change as diagnosed
- [x] Diagnosis records with confidence (step 5): headline and summary in plain English, a 0-100 confidence with high / medium / low (or none, `insufficient_evidence`, when no cause accounts for a quarter of the change or the figure cannot be broken down yet), and a sentence saying how it was reached. From written rules, never a model's opinion: 60% how much of the change the strongest cause explains, 40% how complete the data is, a little more when independent readings agree, less when the cause hides in sales with no product detail, never above 95. Each diagnosis stores the rules version (`diagnosis-1`) and the figures it was built from
- [x] Fact / statistical finding / AI interpretation / insufficient evidence stored separately (step 5: every piece of evidence has its type; facts, statistics and "cannot tell" are produced now; `ai_interpretation` is only ever written by the AI assistant, in Phase 13, and is never produced here)
- [x] Diagnosis explanation in UI (step 6): on the What changed page every change has "Why did this happen?": the headline, the confidence (high / medium / low / not enough evidence) and how it was reached, and the evidence grouped by what kind of statement it is (what your records show, what we worked out from your figures, what the AI assistant thinks, what we can't tell), with the date and rules version. Owners and managers get "Explain this" and "Work it out again"; viewers read explanations already made. Browser-tested on the demo year, including a figure that cannot be broken down yet (honest "not enough evidence")

## Phase 8: Forecasting engine

Design (step 1): a forecast learns from the monthly KPI values the KPI engine stored (it never recalculates them) and is made by plain statistical methods in `app/forecast` (pure functions, no database, **no language model**): the recent average, a straight-line trend, and the same month last year. Each method is **tried on the business's own recent months** (cover up the last few, forecast each from what came before, compare with what happened), the one that missed by the least is used (the simplest if equal), and the size of its misses sets the **range** around every prediction (80% by default, 50-99% on request; it widens with the square root of the months ahead and is never narrower than 2% of the level). At least 6 finished months are needed; with fewer the forecast says so instead of guessing. A figure that follows the trading year is learned with each month's confirmed season taken out and put back into the forecast months. Methods are rows in `forecast_models` (code, plain description, version, months needed); every forecast records which method and version made it and the figures it learned from. Refreshed after every KPI calculation, best-effort.

- [x] Tables: forecasts, forecast_predictions, forecast_models (3 methods seeded), forecast_evaluations (how each method did when tried: typical miss in pounds and per cent, months tested, which was chosen)
- [x] `ForecastService` with swappable models (`app/services/forecast.py`; a method is a small class in `app/forecast/models.py`, a row in `forecast_models`, and nothing else to change)
- [x] Revenue forecast (step 1: next 1-12 months, default 3; `GET /organizations/{id}/forecasts/revenue`, `POST` to make one now (owner or manager), `GET /forecasts` lists what can be forecast). Only sales so far
- [ ] Cash-flow forecast (needs bank or accounting data: the Xero connector)
- [ ] Customer demand forecast
- [ ] Inventory requirements forecast
- [ ] Churn/retention risk forecast
- [x] Confidence interval on every prediction (step 1: a lower and upper value with the level, and the typical miss of each method tried)
- [x] `actual_value` backfill + accuracy tracking (step 2): whenever the figures are worked out (and when a forecast is made) every forecast month that has since finished gets its real figure filled in, a corrected figure is picked up, and each forecast is scored on the months that can be checked (kind "actual" in `forecast_evaluations`, with how many landed inside the range). `GET /organizations/{id}/forecasts/{figure}/accuracy` gives the typical miss in pounds and per cent, which way forecasts lean (too high or too low), how often the real figure landed inside the range against how often it was meant to (and says plainly when the ranges are too narrow), the same by how many months ahead, and every checked month, newest first. Passes no judgement until three forecast months have finished
- [x] Forecast visualisation (step 2: `forecast.html`): recent months as a line, the forecast as a dashed line with its range shaded and widening, a ring where a finished month's real figure is known, a table of expected and range and what happened, how the method was chosen (every method tried and how close each came), the accuracy section, and "Work it out again" with 3, 6 or 12 months for owners and managers
- [~] Rule enforced: LLM never produces the forecast number (nothing in the forecasting code or service reaches an AI provider, and a test checks the service source says so; the rule stays open until the AI assistant exists to be held to it)

## Phase 9: Recommendation engine

- [ ] Tables: recommendations, recommendation_options, recommendation_evidence, intervention_library
- [ ] Intervention library
- [ ] Option generation (diagnosis + goals + forecast + history + constraints)
- [ ] Option evaluation (impact, cost, effort, confidence, history, urgency, goal fit)
- [ ] Ranking / selection scoring
- [ ] Recommended-action selection
- [ ] "Why this one" rationale
- [ ] Rule enforced: ranking is rules + evaluation, not the LLM

## Phase 10: Action management

- [ ] Tables: actions, action_updates, action_evidence, interventions
- [ ] Accept recommendation → intervention
- [ ] Modify before accepting
- [ ] Assign owner
- [ ] Start / target dates
- [ ] Statuses: PENDING, ACCEPTED, IN_PROGRESS, PARTIALLY_COMPLETED, COMPLETED, CANCELLED, OVERDUE
- [ ] Notes
- [ ] Evidence attachments
- [ ] Mark complete
- [ ] Overdue detection

## Phase 11: Follow-up + outcome measurement

- [ ] Tables: intervention_outcomes, follow_up_schedules
- [ ] Background scheduler (*decision needed: no Docker on Windows → Memurai or APScheduler instead of Redis/Celery*)
- [ ] Follow-up notification to the responsible user
- [ ] KPI re-measurement at follow-up
- [ ] Expected vs actual comparison
- [ ] Outcomes: SUCCESSFUL, PARTIALLY_SUCCESSFUL, UNSUCCESSFUL, **INCONCLUSIVE**
- [ ] Intervention report
- [ ] On non-success: generate an alternative recommendation

## ⚑ Milestone: vertical slice proof

Prove the whole loop on one fake retail business before building further.

- [ ] Upload data
- [ ] Calculate KPIs
- [ ] Detect a revenue decline
- [ ] Explain the cause
- [ ] Forecast revenue
- [ ] Generate 3 candidate actions
- [ ] Select and explain a recommendation
- [ ] User accepts it
- [ ] Track the action
- [ ] Run follow-up
- [ ] Measure the outcome
- [ ] Store the learning

## Phase 12: Business memory / learning

- [ ] Tables: business_memory, business_learning, intervention_patterns, memory_retrieval_events
- [ ] Goals, normal KPI ranges, seasonality in memory
- [ ] Successful interventions
- [ ] Unsuccessful interventions
- [ ] Customer-behaviour patterns
- [ ] Historical recommendations + outcomes
- [ ] User preferences
- [ ] Business constraints
- [ ] Similar-case retrieval
- [ ] Memory feeds recommendation evaluation

## Phase 13: AI consultation

- [ ] Tables: conversations, conversation_messages, conversation_context, ai_tool_calls, ai_model_versions
- [ ] Provider wrapper (Claude first, swappable)
- [ ] Chat interface (web)
- [ ] Intent detection
- [ ] Business-context retrieval
- [ ] Grounded tools: health, KPI, trend, diagnosis, forecast, recommendations, actions, outcomes
- [ ] Answers only from tool outputs (never question → LLM → guess)
- [ ] Conversation history
- [ ] Role-based response shaping
- [ ] Respect per-organisation AI/data-use controls

## Phase 14: Alerts + notifications

- [ ] Tables: alert_rules, alerts, alert_events, notification_preferences, notifications
- [ ] Alert rule configuration
- [ ] Severities: INFO, LOW, MEDIUM, HIGH, CRITICAL
- [ ] Types: Sales, Financial, Customer, Inventory, Marketing, Forecast, Action, Data, Security
- [ ] Alert generation
- [ ] Grouping / duplicate suppression
- [ ] Only security alerts un-suppressible
- [ ] Notification Service owns delivery (modules emit events)
- [ ] **Real email provider** (replaces console backend)
- [ ] In-app notifications
- [ ] Mobile push (with Phase 18)
- [ ] Per-user preferences, quiet hours
- [ ] Alert history
- [ ] Alerts link to the relevant analysis screen

## Core UX screens

- [ ] Dashboard: "what needs my attention today" first
- [ ] Business Insight screen: what happened → why → what next → options → recommended → accept
- [ ] Role-differentiated views
- [ ] Shared JS modules (nav, auth, API client, formatters)
- [ ] Consistent page shell across pages (check for drift)

## Phase 15: Reporting

- [ ] Tables: reports, report_runs, report_schedules
- [ ] Health, KPI and intervention-outcome reports
- [ ] PDF and CSV export
- [ ] Scheduled delivery

## Phase 16: Subscription / billing

- [ ] Tables: plans, subscriptions, subscription_items, feature_entitlements, billing_events, invoices
- [ ] Plans configurable in admin (never hard-coded prices)
- [ ] Feature gating per tier
- [ ] Stripe integration
- [ ] Paystack integration (pluggable per organisation)
- [ ] Upgrade / downgrade
- [ ] Billing history

## Phase 17: Admin portal

- [ ] Tables: feature_flags, support_cases, admin_notes, system_events
- [ ] Organisation/tenant management
- [ ] Cross-tenant user/role management
- [ ] Internal system health dashboard
- [ ] Audit log viewer

## Phase 18: Mobile apps

Confirmed in scope (2026-10-01). iOS and Android, one shared backend, same permissions and
tenant rules as the web app. UK-first applies here too: £ only, dd/mm/yyyy, Monday-first
weeks, Europe/London, en-GB wording.

**Before building (backend prerequisites: the API is browser-shaped today)**

- [ ] Mobile sign-in: today the refresh token lives in an httpOnly browser cookie. Phones need the refresh token returned in the response body (for clients that ask for it) and kept in the phone's secure storage (iOS Keychain / Android Keystore), still rotating and revocable per device
- [ ] Per-device sessions: name each session ("Jo's iPhone"), show and revoke them in settings
- [ ] Email links (verify email, reset password, accept invitation) open the app when installed: universal links (iOS) and app links (Android), falling back to the web pages
- [ ] Forgot-password in the app (standing rule: every client has it)
- [ ] API versioning and a minimum-supported-app-version check, so an old app can be told to update
- [ ] Push token registration endpoints (device, platform, last seen) and the sending side (with Phase 14)
- [ ] Rate limits and error messages checked for mobile (flaky connections, retries are safe: idempotent where it matters)

**The app**

- [ ] Flutter project set up in the repo (`mobile/`): Dart tooling installed, lint + tests in CI, Android build in CI, iOS build on a macOS runner
- [ ] Sign-in, sign-up, verify email, forgot/reset password, biometric unlock (Face ID / fingerprint) after first sign-in
- [ ] Business switcher (a person can belong to several businesses)
- [ ] Dashboard (read-only health + KPIs)
- [ ] Alerts and in-app notification list
- [ ] Recommendations: view, approve / reject (Owner; Manager within remit; Viewer read-only)
- [ ] Actions and follow-up: mark done, add outcome
- [ ] AI consultation
- [ ] Data: upload a CSV/Excel file from the phone (share sheet / file picker), see checking results, import and undo; quick manual entry of a sale or expense
- [ ] Push notifications (with Phase 14), with per-category preferences
- [ ] Offline and poor-signal behaviour: last-known data shown with its age, clear "couldn't reach Vyterlix" states, no silent data loss
- [ ] Secure by default: nothing sensitive in logs or screenshots-in-switcher, certificate pinning decision, jailbreak/root decision, app data cleared on sign-out
- [ ] Accessibility (text size, screen readers, contrast) and dark mode
- [ ] Mobile testing: real iOS and Android devices, small and large screens, slow network, interrupted uploads
- [ ] Store release work is tracked under L5 (accounts, signing, listings, privacy labels, TestFlight / internal testing)

---

## L1: Security & privacy hardening

- [ ] HTTPS everywhere
- [ ] Secrets management
- [ ] Encryption at rest and in transit
- [ ] MFA
- [ ] Secure sessions
- [ ] Rate limiting (all public endpoints)
- [ ] Input validation review
- [ ] File-upload security
- [ ] Dependency scanning
- [ ] SQL injection testing
- [ ] XSS/CSRF testing
- [ ] Tenant-isolation penetration tests
- [ ] PostgreSQL row-level security (ADR 0001 §2, layer 2)
- [ ] Data retention
- [ ] Data deletion
- [ ] Data export
- [ ] Privacy policy
- [ ] Terms of service
- [ ] AI/subprocessor disclosure
- [ ] Legal review of the model-improvement opt-in

## L2: Automated QA & testing

- [~] Unit + integration tests per step (ongoing — every step ships with tests)
- [ ] Unit: KPI formulas, health scoring, detection, diagnosis, forecasting, ranking, outcome measurement
- [ ] Integration: database, imports, integrations, background jobs, email, billing, AI provider
- [ ] End-to-end: registration → onboarding → upload → KPI → health → diagnosis → forecast → recommendation → action → outcome → learning; alert → insight; AI → evidence
- [ ] Browser tests for the vanilla-JS frontend (replaces dropped JS unit tests)
- [ ] Load and performance testing
- [ ] Browser compatibility
- [ ] Accessibility
- [ ] Mobile testing

## L3: Production infrastructure

- [ ] Hosting choice (no Docker locally — confirm deployment approach)
- [ ] Staging + production PostgreSQL
- [ ] Background job runner + scheduler
- [ ] File storage
- [ ] Secrets manager
- [ ] Monitoring, error tracking, log aggregation
- [ ] Backups
- [ ] Firewall, private DB access, HTTPS, reverse proxy/load balancer, rate limiting

## L4: Production deployment

- [ ] Backend: Uvicorn/Gunicorn config, production env vars, migrations, deploy, health check
- [ ] Trusted-proxy client IP handling (TODO in `app/api/v1/auth.py`)
- [ ] Frontend: production `config.js`, deploy, domain, SSL
- [ ] CORS allow-list matches deployed frontend
- [ ] Database: production migrations, index check, backup, **restore test**
- [ ] Workers: deploy, verify queues and retries
- [ ] Email: production provider, SPF, DKIM, DMARC, delivery test

## L5: App store release

- [ ] Mac / cloud macOS access for iOS builds (Xcode is macOS-only; the dev machine is Windows)
- [ ] Android: Play developer account, signing, package, listing, privacy info, internal testing, submission
- [ ] iOS: Apple developer account, signing, metadata, privacy info, TestFlight, submission

## L6: UAT & go-live

- [ ] UAT: registration, onboarding, CSV import, KPIs, health, diagnosis, forecast, recommendation, approval, action, follow-up, outcome, learning, AI, alerts, reports, billing, mobile
- [ ] Production smoke test
- [ ] Backup and restore verified
- [ ] Monitoring and error tracking verified
- [ ] Email, billing, background jobs, integration sync verified
- [ ] Security sign-off
- [ ] UAT sign-off
- [ ] Client acceptance
- [ ] Go-live approval

## L7: Post-launch operations

- [ ] Monitoring: uptime, API errors, database, workers, integrations, AI usage, forecast accuracy, recommendation effectiveness
- [ ] Support: workflow, incident severities, incident response, SLA, feedback, bug triage
- [ ] Product analytics: activation, feature usage, recommendation acceptance, intervention completion/outcomes, alert engagement, AI usage
- [ ] Operations: monthly cost review, security review, dependency updates, DB maintenance, backup checks, AI provider review

---

## Non-negotiables (check at every step)

- [ ] Every score/diagnosis/forecast/recommendation has a visible evidence trail
- [x] Tenant isolation decided at Phase 0, in the schema
- [ ] The LLM never computes a number or invents a conclusion
- [ ] Every AI-adjacent record stores data + rule/model version
- [ ] `INCONCLUSIVE` stays a valid outcome
- [ ] No Phase 2/3 brief scope (scenario planning, cross-business learning, digital twin) in MVP
- [ ] Every frontend DOM write of API data checked for XSS
- [ ] Production CORS allow-list matches the real frontend
- [ ] Forgot-password is present and working in every release

## Parked / deferred (so nothing gets lost)

| Item | Why parked | Picked up in |
|---|---|---|
| Staging/prod databases | Nothing to deploy yet | L3 |
| Frontend JS unit tests | No Node | L2 (browser tests) |
| Redis/Celery on Windows without Docker | Not needed until follow-ups | Phase 11 decision |
| Real email provider | Console backend is enough for dev | Phase 14 |
| Client IP behind proxy | Only matters once deployed | L4 |
| Row-level security in PostgreSQL | App-layer isolation first | L1 |
| Custom per-organisation roles | Corporate feature | Post-MVP |
| Industry benchmarks data | Sectors not chosen yet | Phase 3 step 7 / later |
| Refresh-token reuse detection (revoke whole session if an old token is replayed) | Rotation already makes old tokens useless | L1 |
| Clean-up job for old `login_attempts` / expired sessions / used tokens | Tables grow slowly; needs the background-job runner | Phase 11 |
| Email-based lockout can be triggered by an attacker (15-min lockout of a victim) | Standard trade-off; mitigated by forgot-password + short window | L1 review |
| Change email address (verify the new address before switching, alert the old one) | Needs its own verified flow; not required for MVP sign-up | Before L6 (UAT) |
| Limit on businesses one user can create | Abuse guard; ties into plan/tier limits | Phase 16 (billing) |
| Limit on pending invitations per business / invite rate | Abuse guard against using invites to send spam | Phase 16 / L1 |
| **UK GDPR erasure vs audit trail:** audit `details` can hold email addresses and survive account deletion — decide what to anonymise vs keep (legal basis), and build retention clean-up using `vyterlix.allow_audit_delete` | Needs a legal/retention decision | L1 (before launch) |
| Audit-row volume from repeated blocked logins | Edge rate limiting will absorb it | L1 / L3 |
| "My security activity" page for users (their own logins, password changes) | Nice to have | Post-MVP |
| **HTML emails must escape user data** (business/inviter names go into emails verbatim; safe today only because emails are plain text) | Real email provider not chosen yet | Phase 14 |
| Content-Security-Policy and other security headers on the frontend host | Set at the web server/CDN | L1 / L4 |
| Two tabs refreshing at the same instant can log one out (refresh token rotates) | Rare; fix with a short reuse grace window | L1 |
| Frontend screens for members, roles, invitations and audit log (API exists) | Belongs with the business screens | Phase 3 / Core UX |
| **Worker supervision:** the background worker is started by hand (`python -m app.cli.worker run`); in production it needs a service manager that restarts it, and an alert when jobs wait a long time or a worker has not been seen | Add with monitoring and deployment | L3 / Monitoring |
| **Automatic (scheduled) syncs:** connections only update when someone presses "Update now" | Needs a scheduler that queues `integration.sync` jobs (e.g. nightly), plus alerts when a connection stays failing | With the first real connector (Xero) |
| **Who may add data:** only the Owner holds `data.manage`, so Managers cannot upload, type in or connect anything (the Manager role seeded in Phase 2 has insights, recommendations and actions only). The screens now say so. Decide with the client whether Managers should be able to import data, then add the permission to the Manager role in a migration | Product decision for Dolapo Dorcas Falusi | Before the first real user test |
| **Orders with one row per product line** (the same order number on several rows with different products) are reported as a conflict, not combined into one sale with lines | Needs a "group rows by order" option in the mapping | Phase 4 (after the milestone), or when a real file needs it |
| **Mobile-ready API** (device sessions, refresh token in secure storage, app links, push tokens, minimum app version) | Listed under Phase 18; do not build the web-only shortcuts deeper (e.g. cookie-only refresh) without a mobile path | Phase 18 (design check at Phase 14) |
| **Statistics after bulk loads:** a bulk import leaves Postgres thinking the tables are nearly empty, which made undo take minutes and imports erratic; the import now refreshes statistics (ANALYZE) as it grows and before an undo. Any future bulk loader (connectors, Part B) must do the same, and production should check autovacuum settings | Found by timing a 50,000-row run on the real server | Phase 4 Part B / L3 |
| **Seasonal "usual" for a business with under a year of history and no confirmed seasons is still the plain six-month average:** a quiet January after a busy December can score low (the demo year's January and February score about 40-45 until a year exists) | Confirm seasons (onboarding or the suggested ones from Phase 8 detection); from the second year the same month last year is used | Phase 8 (forecasting / seasonality detection) |
| **No industry-specific health rules or sector benchmarks yet:** the starting thresholds are Vyterlix's own, labelled as such | Never load made-up benchmarks; needs real sources | When real benchmarks are loaded |
| **Anomaly detection judges each figure on its own, and only on a monthly basis:** it does not yet look across figures (a slide in sales together with a rise in refunds), weekly patterns, or compare with the same month last year | Cross-figure patterns and same-month-last-year come with segment and driver analysis | Phase 7 step 3+ |
| **Change detection reports pairs that are really one change** (retention and churn always move together; revenue and takings including VAT; profit and its margin) and orders mixed units (per cent and points) by size | Group related figures into one finding; rank by effect on the business | Phase 7 step 3 (segment/driver work) |
| **Diagnosis is made on request, not automatically after detection, and only explains moves against the month before:** an unusual month that did not move much on last month is reported as "not enough evidence" rather than broken down | Make diagnoses automatically for the biggest changes (with alerts, Phase 14); explain a month against its history | Phase 7 step 6 / Phase 14 |
| **Confidence weights (60/40, bonuses, cap of 95) are Vyterlix's own starting rules**, not calibrated against outcomes | Calibrate with real outcomes once follow-up tracking (Phase 11) has data | Phase 11-12 |
| **Forecasts only cover sales, and only from a plain recent-average, straight-line or same-month-last-year method:** no promotions, price changes, stock-outs or outside events, and none for cash, customers or stock yet | More methods and figures; accuracy tracking will show where it misses | Phase 8 next steps |
| **Forecast needs 6 finished months, and a seasonal method needs 18** (a year to repeat plus months to test it on): a new business gets a wide, plain forecast | Improves as history builds; the owner's confirmed seasons help meanwhile | Ongoing |
| **Detection thresholds (15% / 30%, 3 / 8 points) are Vyterlix's own starting values**, the same for every business and sector | Tune per sector once real benchmarks and real customer data exist | When real data is available |
| **Data-quality extras:** unusual amounts (a sale far above normal), unusual days (a quiet day inside a busy month), per-product stock-out gaps, and scoring per connected system once connectors exist | Needs enough real history to know what is "unusual"; false alarms would erode trust | Phase 5 (KPI engine) / Part B |
| **Browser caching of JS/CSS in production** (stale code after a deploy: seen in dev with the plain Python server) | Needs cache headers or versioned file names at the web host | L4 |
| Manage members/roles and view the audit log from `business.html` (currently only invite in the wizard) | APIs exist; screens not built yet | Core UX |
