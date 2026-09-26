# Vyterlix — Build Checklist

The single tracker for the whole build. Tick items **in the same commit as the work**, so
this file always matches `main`.

`[x]` done · `[ ]` to do · `[~]` partly done (see note) · **→** moved elsewhere (see where)

Sources: `VYTERLIX_IMPLEMENTATION_CHECKLIST.md` (build order),
`Vyterlix_Master_Implementation_Checklist.md` (launch phases), PRD and brief.

## Progress

| Phase | Name | Status |
|---|---|---|
| −1 | Decisions before code | 18 / 20 decided |
| 0 | Architecture + database design | In progress |
| 1 | Project foundation | Done |
| 2 | Authentication + multi-tenancy | Done |
| 3 | Business onboarding & profile | **In progress — 1 of 9 steps done** |
| 4 | Data import + normalisation | Not started |
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
| 18 | Mobile apps | Not started |
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
- [x] Local static server documented (`python -m http.server 5500`)
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
- [ ] 2. Business profile API (name, industry, size, country, currency, model, years operating, team size, fiscal-year start)
- [ ] 3. Business goals (type, target KPI/value/date, priority)
- [ ] 4. Products/services, sales channels, customer types, major cost categories
- [ ] 5. Seasonality (peak/quiet periods)
- [ ] 6. Business settings + notification preferences (timezone, week start, quiet hours, channels)
- [ ] 7. Benchmark storage (by industry/size/region; empty until sectors known)
- [ ] 8. Onboarding progress tracking
- [ ] 9. Frontend: onboarding wizard + business settings page

## Phase 4: Data import + normalisation

- [ ] Tables: customers, products, sales, sale_items, expenses, inventory, inventory_transactions, suppliers, data_sources, data_imports, data_import_rows, data_quality_issues
- [ ] CSV upload
- [ ] Excel upload
- [ ] Column-mapping UI
- [ ] Validation on import
- [ ] Duplicate detection
- [ ] Error reporting on failed rows
- [ ] Import history
- [ ] Manual data entry (system works with zero integrations)
- [ ] Normalisation onto the canonical transaction model
- [ ] Data-quality scoring per source/period
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

- [ ] Flutter app (iOS + Android, one codebase)
- [ ] Dashboard (read-only health + KPIs)
- [ ] Alerts
- [ ] AI consultation
- [ ] Recommendation approve/reject
- [ ] Push notifications

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
