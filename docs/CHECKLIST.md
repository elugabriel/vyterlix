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
| 4 | Data import + normalisation | **In progress — Part A step 9 of 9 done (milestone dataset + Part B next)** |
| 5 | KPI engine | Not started |
| 6 | Business health engine | Not started |
| 7 | Diagnostic engine | Not started |
| 8 | Forecasting engine | Not started |
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
- [ ] Integration framework: abstraction, OAuth/token handling, encrypted credentials, sync status, last successful sync, sync errors, re-authentication, permission summary, provenance
- [ ] Connector: **Xero** (accounting)
- [ ] Connector: **Shopify**
- [ ] Connector: **WooCommerce**
- [ ] Connector: **Google Analytics**

**Done when:** Sales/Expenses/Customers/Inventory CSVs become normalised records.

## Phase 5: KPI / business intelligence engine

- [ ] Tables: kpi_definitions, kpi_values, kpi_calculation_runs
- [ ] KPI engine as a service (not in route handlers)
- [ ] KPI definitions stored as data, not hard-coded
- [ ] Financial: revenue, gross/net profit & margin, operating expenses, cash position/runway
- [ ] Sales: growth rate, AOV, sales by channel/product
- [ ] Customer: active count, new/returning, repeat rate, churn, retention, CAC, CLV
- [ ] Marketing: spend, conversion rate, ROAS/CAC by channel, traffic
- [ ] Inventory: stock level, turnover, stockout rate, COGS, fast/slow movers, dead stock
- [ ] KPI history storage
- [ ] Period-over-period comparison

## Phase 6: Business health engine

- [ ] Tables: business_health, business_health_components, health_rules
- [ ] Categories: Financial, Sales, Customer, Marketing, Inventory, Operational
- [ ] Per-business baseline ("normal" for this business)
- [ ] Configurable health rules (weights/thresholds per industry)
- [ ] Weighted overall score
- [ ] Trend per component (↑ ↓ →)
- [ ] Explanation stored for every component (click-through to evidence)
- [ ] Health history
- [ ] Health dashboard UI

## Phase 7: Diagnostic / explanation engine

- [ ] Tables: diagnoses, diagnostic_evidence, detection_events
- [ ] Material-change detection
- [ ] Anomaly detection
- [ ] Segment analysis (product, customer, location, time, channels, price, quantity, cost category, inventory)
- [ ] Driver/contributor identification
- [ ] Evidence generation + storage
- [ ] Diagnosis records with confidence
- [ ] Fact / statistical finding / AI interpretation / insufficient evidence stored separately
- [ ] Diagnosis explanation in UI

## Phase 8: Forecasting engine

- [ ] Tables: forecasts, forecast_predictions, forecast_models, forecast_evaluations
- [ ] `ForecastService` with swappable models
- [ ] Revenue forecast
- [ ] Cash-flow forecast
- [ ] Customer demand forecast
- [ ] Inventory requirements forecast
- [ ] Churn/retention risk forecast
- [ ] Confidence interval on every prediction
- [ ] `actual_value` backfill + accuracy tracking
- [ ] Forecast visualisation
- [ ] Rule enforced: LLM never produces the forecast number

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
| **Very large files are checked and imported inside the web request** (measured: 50,000 rows take about 17 s to check and 60 s to import, so about 5 minutes to import at the 250,000-row limit: too slow for a browser or proxy timeout) | Needs the PostgreSQL-backed background job queue planned for Part B | Phase 4 Part B (before the upload page ships for big files) |
| **Orders with one row per product line** (the same order number on several rows with different products) are reported as a conflict, not combined into one sale with lines | Needs a "group rows by order" option in the mapping | Phase 4 (after the milestone), or when a real file needs it |
| **Mobile-ready API** (device sessions, refresh token in secure storage, app links, push tokens, minimum app version) | Listed under Phase 18; do not build the web-only shortcuts deeper (e.g. cookie-only refresh) without a mobile path | Phase 18 (design check at Phase 14) |
| **Statistics after bulk loads:** a bulk import leaves Postgres thinking the tables are nearly empty, which made undo take minutes and imports erratic; the import now refreshes statistics (ANALYZE) as it grows and before an undo. Any future bulk loader (connectors, Part B) must do the same, and production should check autovacuum settings | Found by timing a 50,000-row run on the real server | Phase 4 Part B / L3 |
| **Data-quality extras:** unusual amounts (a sale far above normal), unusual days (a quiet day inside a busy month), per-product stock-out gaps, and scoring per connected system once connectors exist | Needs enough real history to know what is "unusual"; false alarms would erode trust | Phase 5 (KPI engine) / Part B |
| **Browser caching of JS/CSS in production** (stale code after a deploy: seen in dev with the plain Python server) | Needs cache headers or versioned file names at the web host | L4 |
| Manage members/roles and view the audit log from `business.html` (currently only invite in the wizard) | APIs exist; screens not built yet | Core UX |
