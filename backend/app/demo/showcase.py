# ruff: noqa: E501  (long lines of demo data are easier to read whole)
"""Fill a demo business with demo information everywhere, so every screen of Vyterlix has something
real-looking to show when it is tried out.

    python -m app.cli.demo showcase --org <business id> [--org <another>]
    python -m app.cli.demo unshowcase      # take the demo-only extras (benchmarks) out again

Everything here is invented. It only goes into a business whose name ends with "(demo data)", it
refuses to run unless the environment is "dev", and it is meant to be removed before the system is
hosted. It works through the same API the screens use (so the same checks apply) wherever it can.

What it adds, beyond the year of trading that `load` brings in:
- the business's own set-up: profile, goals, busy and quiet seasons, lists, settings, notification
  choices, and a finished onboarding;
- a team: a manager and a viewer who can log in, and invitations still waiting;
- sector benchmarks, every one labelled DEMO in its source (they are invented, not published);
- a practice connection (the "Sandbox") with a sync history;
- an upload history that shows every state: imported, undone, checked with mistakes found;
- a few things typed in by hand;
- the key figures, health score, changes, explanations and forecasts worked out, and forecasts made
  as if at earlier months so the accuracy section has months to check.

Logins for the demo people are written to backend/.demo-logins.local.md (not committed).
"""

import io
import secrets
import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.integrations.base import utcnow
from app.main import create_app
from app.models.business import BusinessBenchmark
from app.models.identity import Organization, OrganizationUser, Role, User

DEMO_SUFFIX = "(demo data)"
BENCHMARK_SOURCE = "DEMO DATA: invented for testing, not a published figure"
EMAIL_DOMAIN = "fakeham-bakery.example"  # a reserved name: nothing can ever be delivered to it
LOGINS_FILE = Path(__file__).resolve().parents[2] / ".demo-logins.local.md"  # not committed
OWNER_EMAIL = "e2e-ui@acme.co.uk"  # the demo owner that already owns the demo businesses
TEAM = (
    ("manager", f"manager@{EMAIL_DOMAIN}", "Maya Manager (demo)"),
    ("viewer", f"viewer@{EMAIL_DOMAIN}", "Victor Viewer (demo)"),
)


class ShowcaseError(Exception):
    """Something the person running it can fix; shown without a stack trace."""


def _need_dev() -> None:
    if get_settings().env != "dev":
        raise ShowcaseError("Refusing: demo information only goes into a development system.")


def _org(db: Session, org_id: uuid.UUID) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:
        raise ShowcaseError("No business with that id.")
    if not org.name.rstrip().endswith(DEMO_SUFFIX):
        raise ShowcaseError(
            f'Refusing: "{org.name}" is not a demo business '
            f'(its name must end with "{DEMO_SUFFIX}").'
        )
    return org


# --- people ----------------------------------------------------------------------------------------


def _ensure_user(db: Session, email: str, name: str, password: str) -> User:
    user = db.scalars(select(User).where(User.email == email)).first()
    now = utcnow()
    if user is None:
        user = User(
            email=email, full_name=name, password_hash=hash_password(password),
            email_verified_at=now, password_changed_at=now,
        )  # fmt: skip
        db.add(user)
    else:
        user.password_hash = hash_password(password)
        user.password_changed_at = now
        user.email_verified_at = user.email_verified_at or now
    db.flush()
    return user


def _ensure_member(db: Session, org_id: uuid.UUID, user: User, role_code: str) -> None:
    role = db.scalars(select(Role).where(Role.code == role_code)).one()
    with tenant_scope(db, org_id):
        member = db.scalars(
            select(OrganizationUser).where(OrganizationUser.user_id == user.id)
        ).first()
        if member is None:
            db.add(OrganizationUser(user_id=user.id, role_id=role.id, status="active"))
        else:
            member.role_id, member.status = role.id, "active"
        db.flush()


def _people(
    db: Session, org_ids: list[uuid.UUID], passwords: dict[str, str], also_owners: list[str]
) -> None:
    owner = _ensure_user(db, OWNER_EMAIL, "Demo Owner", passwords["owner"])
    people = {role: _ensure_user(db, email, name, passwords[role]) for role, email, name in TEAM}
    for org_id in org_ids:
        _ensure_member(db, org_id, owner, "owner")
        for role, user in people.items():
            _ensure_member(db, org_id, user, role)
        for email in also_owners:  # someone's own login, so they see the demo businesses too
            person = db.scalars(select(User).where(User.email == email.lower())).first()
            if person is None:
                raise ShowcaseError(f"No one has registered with {email}.")
            _ensure_member(db, org_id, person, "owner")
    db.commit()


def _write_logins(passwords: dict[str, str]) -> None:
    lines = [
        "# Demo logins (local only: this file is not committed)",
        "",
        "All demo. Delete these accounts before the system is hosted.",
        "",
        "| Role | Email | Password |",
        "|---|---|---|",
        f"| Owner | {OWNER_EMAIL} | {passwords['owner']} |",
    ]
    for role, email, _ in TEAM:
        lines.append(f"| {role.title()} | {email} | {passwords[role]} |")
    LOGINS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the API, as a person ---------------------------------------------------------------------------


class Api:
    """Calls the real API as one person."""

    def __init__(self, client: TestClient, email: str, password: str):
        res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        if res.status_code != 200:
            raise ShowcaseError(f"Could not log in as {email}: {res.text[:200]}")
        self.client = client
        self.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def call(self, method: str, path: str, ok=(200, 201, 202, 204), **kw):
        res = self.client.request(method, f"/api/v1{path}", headers=self.headers, **kw)
        if res.status_code not in ok:
            raise ShowcaseError(f"{method} {path} -> {res.status_code}: {res.text[:300]}")
        return res.json() if res.content else None


# --- the business's own set-up -------------------------------------------------------------------------

PROFILE = {
    "industry_code": "retail",
    "financial_year_start": {"month": 4, "day": 1},
    "sic_code": "10710",
    "region": "south_east",
    "town_city": "Brighton",
    "postcode": "BN1 1AA",
    "business_size": "small",
    "business_model": "b2b_and_b2c",
    "team_size": 9,
    "founded_year": 2014,
    "vat_registered": True,
    "vat_number": "GB123456789",
}
# (title, type, KPI code, unit, target, where it started, priority)
GOALS = (
    ("Grow sales by 15% this year", "increase_revenue", "revenue_growth_pct", "percent", "15", "0", 1),
    ("Lift profit margin to 12%", "improve_margin", "net_margin_pct", "percent", "12", "8", 2),
    ("Keep 60% of customers coming back", "improve_retention", "customer_retention_pct", "percent", "60", "50", 2),
    ("Cut wasted stock", "reduce_stock_problems", "out_of_stock_pct", "percent", "5", "12", 3),
    ("Win 40 new regular customers", "grow_customers", "new_customers", "count", "40", "0", 3),
)  # fmt: skip
SEASONS = (
    ("Christmas", (12, 1), (1, 5), 40, "Mince pies, yule logs and gift boxes."),
    ("Easter", (3, 20), (4, 12), 25, "Hot cross buns and Easter biscuits."),
    (
        "Summer holidays",
        (7, 20),
        (8, 31),
        -15,
        "Fewer regulars, more tourists but smaller baskets.",
    ),
    ("Back to school", (9, 1), (9, 14), 10, "Lunchbox orders pick up."),
    ("January lull", (1, 6), (2, 10), -20, "Everyone is on a diet and a budget."),
)
CUSTOMER_TYPES = ("Walk-in", "Regular", "Wholesale", "Corporate catering")
SALES_CHANNELS = ("Delivery app",)
COST_CATEGORIES = ("Delivery costs", "Equipment hire")

# Invented sector figures for a bakery-type shop. Every row says DEMO in its source.
BENCHMARKS = (
    ("net_margin_pct", "7.5", "percent"),
    ("gross_margin_pct", "55", "percent"),
    ("customer_retention_pct", "52", "percent"),
    ("refund_rate_pct", "2.5", "percent"),
    ("average_order_value", "6.20", "gbp"),
    ("stock_turnover", "0.45", "ratio"),
)


def _lists_by_kind(api: Api, base: str) -> dict[str, set[str]]:
    return {
        kind: {i["name"] for i in items} for kind, items in api.call("GET", f"{base}/lists").items()
    }


def _setup(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    base = f"/organizations/{org_id}"
    say("  business profile, goals, seasons and lists")
    api.call("PUT", f"{base}/profile", json=PROFILE)
    have = {g["title"] for g in api.call("GET", f"{base}/goals")}
    for title, goal_type, kpi, unit, target, baseline, priority in GOALS:
        if title not in have:
            api.call(
                "POST", f"{base}/goals",
                json={"title": title, "goal_type": goal_type, "kpi_code": kpi, "target_unit": unit,
                      "target_value": target, "baseline_value": baseline, "priority": priority,
                      "target_date": "2027-03-31"},
            )  # fmt: skip
    seen = {s["name"] for s in api.call("GET", f"{base}/seasons")}
    for name, start, end, pct, notes in SEASONS:
        if name not in seen:
            api.call(
                "POST", f"{base}/seasons",
                json={"name": name, "start": {"month": start[0], "day": start[1]},
                      "end": {"month": end[0], "day": end[1]}, "expected_change_pct": pct, "notes": notes},
            )  # fmt: skip
    have = _lists_by_kind(api, base)
    for kind, names in (("customer_type", CUSTOMER_TYPES), ("sales_channel", SALES_CHANNELS)):
        missing = [n for n in names if n not in have.get(kind, set())]
        if missing:
            api.call("POST", f"{base}/lists/{kind}/bulk", json={"names": missing})
    for name in COST_CATEGORIES:
        if name not in have.get("cost_category", set()):
            api.call(
                "POST",
                f"{base}/lists/cost_category",
                json={"name": name, "is_cost_of_sales": False},
            )
    say("  settings and notification choices")
    api.call(
        "PATCH",
        f"{base}/settings",
        json={"week_start_day": 1, "quiet_hours": {"start": "21:00:00", "end": "07:00:00"}},
    )
    api.call(
        "PATCH", f"{base}/notification-preferences",
        json={"preferences": {"sales": {"email": True, "in_app": True, "push": True},
                              "forecast": {"email": True, "in_app": True},
                              "inventory": {"email": False, "in_app": True, "push": False}}},
    )  # fmt: skip


def _invitations(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    say("  invitations waiting to be accepted")
    base = f"/organizations/{org_id}/invitations"
    sent = {i["email"] for i in api.call("GET", base)}
    for email, role in (
        (f"new.baker@{EMAIL_DOMAIN}", "viewer"),
        (f"accounts@{EMAIL_DOMAIN}", "manager"),
        (f"old.invite@{EMAIL_DOMAIN}", "viewer"),
    ):
        if email not in sent:
            made = api.call("POST", base, json={"email": email, "role": role})
            if email.startswith("old."):
                api.call("DELETE", f"{base}/{made['id']}")  # one that was withdrawn


def _benchmarks(db: Session) -> int:
    db.execute(delete(BusinessBenchmark).where(BusinessBenchmark.source == BENCHMARK_SOURCE))
    for year in (2024, 2025):
        for code, value, unit in BENCHMARKS:
            drift = Decimal("0.98") if year == 2024 else Decimal("1")
            db.add(
                BusinessBenchmark(
                    industry_code="retail", sic_code=None, region=None, size_band="small",
                    kpi_code=code, period_year=year,
                    value=(Decimal(value) * drift).quantize(Decimal("0.0001")), unit=unit,
                    source=BENCHMARK_SOURCE, source_url=None,
                    notes="Invented so the benchmark screens have something to show. Remove before hosting.",
                )
            )  # fmt: skip
    db.commit()
    return len(BENCHMARKS) * 2


def _finish_onboarding(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    say("  finishing onboarding")
    api.call("POST", f"/organizations/{org_id}/onboarding/complete")


# --- a practice connection ------------------------------------------------------------------------------


def _connection(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    say("  a practice connection")
    base = f"/organizations/{org_id}/integrations"
    live = [
        i
        for i in api.call("GET", base)
        if i["provider"] == "sandbox" and i["status"] != "disconnected"
    ]
    if not live:
        started = api.call("POST", f"{base}/connect", json={"provider": "sandbox"})
        state = started["authorize_url"].split("state=")[-1]
        api.call("POST", f"{base}/callback", json={"state": state, "code": "sandbox-code"})
    for connection in api.call("GET", base):
        if connection["provider"] == "sandbox" and connection["status"] == "connected":
            for _ in range(2):  # two updates, so there is a history to show
                api.call("POST", f"{base}/{connection['id']}/sync", ok=(200, 202, 409))


# --- uploads and things typed by hand -----------------------------------------------------------------------

HEADINGS = "Date,Receipt No,Item,Qty,Customer Email,Channel,Total (inc VAT),VAT,Cost\n"
MISTAKES_CSV = HEADINGS + (
    "12/09/2026,DEMO-1001,Demo Croissant,2,,Shop,£4.20,£0.70,£0.96\n"
    "13/09/2026,DEMO-1002,Demo Sourdough Loaf,1,,Website,£4.50,£0.75,£1.10\n"
    "31/02/2026,DEMO-1003,Demo Croissant,1,,Shop,£2.10,£0.35,£0.48\n"  # a date that does not exist
    '14/09/2026,DEMO-1004,Demo Flat White,2,,Shop,"£6,50",£1.00,£0.90\n'  # a comma used for pence
    "15/09/2026,DEMO-1005,Demo Birthday Cake,1,,Market stall,£24.00,£4.00,£7.00\n"
    "15/09/2026,DEMO-1005,Demo Birthday Cake,1,,Market stall,£24.00,£4.00,£7.00\n"  # twice
)
SMALL_CSV = HEADINGS + (
    "02/10/2026,DEMO-2001,Demo Croissant,2,,Shop,£4.20,£0.70,£0.96\n"
    "02/10/2026,DEMO-2002,Demo Sourdough Loaf,1,,Website,£4.50,£0.75,£1.10\n"
)


def _upload_and_check(
    api: Api, org_id: uuid.UUID, filename: str, body: str, existing: dict | None
) -> str:
    base = f"/organizations/{org_id}/imports"
    if existing is None:
        existing = api.call(
            "POST", base, data={"dataset": "sales"},
            files={"file": (filename, io.BytesIO(body.encode("utf-8-sig")), "text/csv")},
        )  # fmt: skip
    ident = existing["id"]
    if existing["status"] in ("uploaded", "mapped"):
        mapping = api.call("GET", f"{base}/{ident}/mapping")["suggested_mapping"]
        api.call(
            "PUT", f"{base}/{ident}/mapping",
            json={"mapping": mapping, "options": {"vat_inclusive": True}},
        )  # fmt: skip
        api.call("POST", f"{base}/{ident}/validate")
    return ident


def _history(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    say("  an upload history with every state (done, undone, checked with mistakes)")
    base = f"/organizations/{org_id}/imports"
    have = {i["original_filename"]: i for i in api.call("GET", base)}
    _upload_and_check(
        api,
        org_id,
        "demo-sales-with-mistakes.csv",
        MISTAKES_CSV,
        have.get("demo-sales-with-mistakes.csv"),
    )
    small = have.get("demo-sales-small-undone.csv")
    ident = _upload_and_check(api, org_id, "demo-sales-small-undone.csv", SMALL_CSV, small)
    if small is None:
        api.call("POST", f"{base}/{ident}/import")
        api.call("POST", f"{base}/{ident}/undo")


def _typed_in(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    say("  a few things typed in by hand")
    base = f"/organizations/{org_id}"
    name = "Fakeham Flour Mill (demo)"
    if any(s["name"] == name for s in api.call("GET", f"{base}/suppliers")["items"]):
        return
    supplier = api.call("POST", f"{base}/suppliers", json={"name": name})
    api.call(
        "POST",
        f"{base}/customers",
        json={
            "name": "Brighton Beach Cafe (demo)",
            "email": f"cafe@{EMAIL_DOMAIN}",
            "postcode": "BN2 1TW",
        },
    )
    api.call(
        "POST",
        f"{base}/expenses",
        json={
            "spent_on": "2026-09-20",
            "amount": "85.00",
            "amount_includes_vat": False,
            "vat_rate": "20",
            "description": "Flour delivery (demo)",
            "supplier_id": supplier["id"],
        },
    )
    api.call(
        "POST",
        f"{base}/sales",
        json={
            "sold_on": "2026-09-29",
            "amount": "42.50",
            "amount_includes_vat": False,
            "vat_rate": "20",
            "notes": "Typed in by hand (demo)",
            "lines": [
                {"description": "Celebration cake (demo)", "quantity": "1", "net_amount": "42.50"}
            ],
        },
    )


def _run_waiting_jobs(db: Session, say: Callable[[str], None]) -> None:
    """Do the background work the steps above queued (the connection's updates)."""
    from app.services import jobs
    from app.services.storage import get_file_storage

    storage, done = get_file_storage(), 0
    while jobs.work_once(db, "demo-worker", storage=storage) is not None:
        done += 1
    say(f"  background jobs run: {done}")


def _stock(api: Api, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    """A business with no stock records gets some, so the stock figures have something to show."""
    base = f"/organizations/{org_id}"
    if api.call("GET", f"{base}/stock-movements")["total"]:
        return
    say("  stock deliveries and write-offs")
    products = api.call("GET", f"{base}/products")["items"]
    for number, product in enumerate(products):
        opening = 150 + 30 * (number % 4)
        api.call(
            "POST",
            f"{base}/stock-movements",
            json={
                "product_id": product["id"],
                "moved_on": "2025-10-01",
                "kind": "opening",
                "quantity": str(opening),
                "unit_cost": "0.80",
            },
        )
        for month in range(10, 13):
            api.call(
                "POST",
                f"{base}/stock-movements",
                json={
                    "product_id": product["id"],
                    "moved_on": f"2025-{month}-15",
                    "kind": "delivery",
                    "quantity": "120",
                    "unit_cost": "0.80",
                },
            )
        for month in range(1, 10):
            api.call(
                "POST",
                f"{base}/stock-movements",
                json={
                    "product_id": product["id"],
                    "moved_on": f"2026-{month:02d}-15",
                    "kind": "delivery",
                    "quantity": "90",
                    "unit_cost": "0.80",
                },
            )
            api.call(
                "POST",
                f"{base}/stock-movements",
                json={
                    "product_id": product["id"],
                    "moved_on": f"2026-{month:02d}-28",
                    "kind": "write_off",
                    "quantity": "-12",
                    "notes": "Past its best (demo)",
                },
            )


# --- working everything out ----------------------------------------------------------------------------------------


def _analytics(api: Api, db: Session, org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    from app.services import detection, forecast, health, kpi
    from app.services.jobs import JobTenant

    say("  key figures, health, changes, explanations and forecasts")
    base = f"/organizations/{org_id}"
    owner = db.scalars(
        select(User).where(User.email == OWNER_EMAIL).execution_options(**ACROSS_TENANTS)
    ).one()
    tenant = JobTenant(organization_id=org_id, user=owner)
    with tenant_scope(db, org_id):
        kpi.calculate(db, tenant, granularity="month", trigger="manual")
        health.calculate(db, tenant)
        detection.detect(db, tenant)
        # Forecasts made as if at earlier months, so the accuracy section has months to check
        for month in range(3, 9):
            forecast.calculate(db, tenant, "revenue", 3, as_of=date(2026, month, 1))
        forecast.calculate_all(db, tenant)
    events = api.call("GET", f"{base}/changes?limit=200")
    chosen = []
    for kind in ("material_change", "anomaly"):
        chosen += [e for e in events if e["kind"] == kind and e["severity"] == "major"][:8]
    for event in chosen:
        api.call("POST", f"{base}/changes/{event['id']}/diagnosis")


# --- the whole thing ------------------------------------------------------------------------------------------------


def run(
    org_ids: list[uuid.UUID],
    say: Callable[[str], None] = print,
    also_owners: list[str] | None = None,
) -> None:
    _need_dev()
    passwords = {role: secrets.token_urlsafe(12) for role in ("owner", "manager", "viewer")}
    sessions = get_sessionmaker()
    with sessions() as db:
        for org_id in org_ids:
            _org(db, org_id)
        say("People and sector benchmarks (invented, labelled DEMO)...")
        _people(db, org_ids, passwords, also_owners or [])
        say(f"  {_benchmarks(db)} benchmark figures")
    _write_logins(passwords)
    client = TestClient(create_app())
    owner = Api(client, OWNER_EMAIL, passwords["owner"])
    for org_id in org_ids:
        with sessions() as db:
            say(db.get(Organization, org_id).name)
        _setup(owner, org_id, say)
        _invitations(owner, org_id, say)
        _finish_onboarding(owner, org_id, say)
        _connection(owner, org_id, say)
        _history(owner, org_id, say)
        _typed_in(owner, org_id, say)
        _stock(owner, org_id, say)
        with sessions() as db:
            _run_waiting_jobs(db, say)
        with sessions() as db:
            _analytics(owner, db, org_id, say)
    say(f"Done. Logins are in {LOGINS_FILE.name} (not committed).")


def remove(say: Callable[[str], None] = print) -> None:
    """Take out what lives outside any one business: the demo benchmarks."""
    _need_dev()
    with get_sessionmaker()() as db:
        gone = db.execute(
            delete(BusinessBenchmark).where(BusinessBenchmark.source == BENCHMARK_SOURCE)
        ).rowcount
        db.commit()
    say(f"Removed {gone} demo benchmark figures. Delete the demo accounts and businesses by hand.")
