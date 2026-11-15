# ruff: noqa: E501
"""A made-up hair and beauty salon that also sells a few products, to show every part of Vyterlix.

Nothing here is real. It only ever goes into a business whose name ends with "(demo data)" and only
into a development system. A year of trading (1 November 2025 to 31 October 2026) with
appointments (how long they took, ratings, cancellations, no-shows), the team's hours, sales and
invoices, costs, stock, website visits, bank movements and leads, so that every figure and module a
service-and-products business sees has something to show.

    python -m app.cli.demo salon     # builds the business and everything in it
    python -m app.cli.demo unsalon   # takes the business out again
"""

import random
import uuid
from calendar import monthrange
from collections.abc import Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.demo import showcase
from app.main import create_app
from app.models.business import BusinessListItem
from app.models.data import (
    AdSpend,
    BankTransaction,
    Booking,
    Customer,
    Expense,
    Opportunity,
    Product,
    Sale,
    SaleLine,
    StaffHours,
    StockMovement,
    Supplier,
    WebVisit,
)
from app.models.identity import Organization, User

NAME = "Glow Hair & Beauty (demo data)"
START, END = date(2025, 11, 1), date(2026, 10, 31)
TODAY = date(2026, 11, 15)  # a few appointments after this are "still to come"
EMAIL_DOMAIN = showcase.EMAIL_DOMAIN
D = Decimal


def _money(value) -> Decimal:
    return D(value).quantize(D("0.01"), rounding=ROUND_HALF_UP)


# (name, price before VAT, minutes planned, share of the price spent on materials, who does it)
SERVICES = (
    ("Cut and style", 38, 45, 0.06, ("Amara", "Ben")),
    ("Colour", 85, 120, 0.28, ("Chloe",)),
    ("Beard trim", 18, 20, 0.04, ("Ben",)),
    ("Blow dry", 28, 30, 0.05, ("Amara", "Chloe")),
    ("Highlights", 110, 150, 0.30, ("Chloe",)),
    ("Kids cut", 20, 30, 0.05, ("Amara", "Ben")),
    ("Nails", 32, 45, 0.15, ("Dev",)),
    ("Facial", 48, 60, 0.18, ("Dev",)),
)
STAFF = ("Amara", "Ben", "Chloe", "Dev")
# (name, sku, price before VAT, cost, how many to start with, how many each delivery)
PRODUCTS = (
    ("Argan oil treatment", "GL-ARG", 24, 11, 40, 5),  # sells fastest: the one to watch
    ("Repair shampoo", "GL-SHA", 16, 7, 60, 12),
    ("Repair conditioner", "GL-CON", 16, 7, 60, 12),
    ("Beard balm", "GL-BAL", 14, 6, 30, 6),
    ("Heat protect spray", "GL-HEA", 18, 8, 40, 8),
    ("Hand cream", "GL-HAN", 12, 5, 40, 8),
    ("Nail oil", "GL-NAI", 10, 4, 40, 6),
    ("Gift set", "GL-GIF", 35, 16, 20, 3),  # runs out by the autumn
)
FIRST = (
    "Aisha Beth Chloe Dana Ella Freya Grace Hana Isla Jade Kira Lily Maya Nia Olive Priya Quinn Rosa "
    "Sara Tia Uma Vera Willa Yara Zoe Amy Bella Cara Demi Erin Fay Gia Holly Ivy Jess Kate Leah Mia "
    "Noor Orla Pia Ruth Skye Tess Una Vicky Wren Zara Adam Ben Cal Dan Eli Finn Gus Hugh Ian Jon Kai "
    "Liam Max Ned Omar Paul Raj Sam Tom Zak"
).split()
LAST = (
    "Ahmed Baker Clarke Davies Evans Foster Green Hughes Iqbal Jones Khan Lewis Morgan Nash Owen "
    "Patel Quinn Reid Shah Taylor Usher Walker Young Adams Brown Cole Dixon Ellis Frost Grant Hall"
).split()
# month -> how busy (1 is a normal month)
BUSY = {
    11: 1.0,
    12: 1.2,
    1: 0.85,
    2: 0.92,
    3: 1.0,
    4: 1.03,
    5: 1.08,
    6: 1.08,
    7: 1.0,
    8: 0.97,
    9: 1.04,
    10: 1.06,
}


def _need_dev() -> None:
    if get_settings().env != "dev":
        raise showcase.ShowcaseError(
            "Refusing: demo information only goes into a development system."
        )


def _passwords() -> dict[str, str]:
    """The demo logins the showcase wrote earlier (not committed)."""
    found = {}
    if showcase.LOGINS_FILE.exists():
        for line in showcase.LOGINS_FILE.read_text(encoding="utf8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0].lower() in ("owner", "manager", "viewer"):
                found[cells[0].lower()] = cells[2]
    if "owner" not in found or "manager" not in found:
        raise showcase.ShowcaseError(
            "The demo logins are missing. Run `python -m app.cli.demo showcase --org <id>` once first."
        )
    return found


def _find(db: Session) -> Organization | None:
    return db.scalars(select(Organization).where(Organization.name == NAME)).first()


# --- the business --------------------------------------------------------------------------------


def _create(owner: "showcase.Api", db: Session, say: Callable[[str], None]) -> uuid.UUID:
    made = owner.call("POST", "/organizations", json={"name": NAME})
    org_id = uuid.UUID(made["id"])
    for role, email, _name in showcase.TEAM:
        user = db.scalars(select(User).where(User.email == email)).first()
        if user is not None:
            showcase._ensure_member(db, org_id, user, role)
    db.commit()
    say(f"Created {NAME}")
    return org_id


def _profile(owner: "showcase.Api", org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    base = f"/organizations/{org_id}"
    say("  profile, seasons, goals and settings")
    owner.call(
        "PUT",
        f"{base}/profile",
        json={
            "industry_code": "health_beauty",
            "sells": "both",
            "financial_year_start": {"month": 4, "day": 1},
            "sic_code": "96020",
            "region": "south_east",
            "town_city": "Brighton",
            "postcode": "BN1 1AA",
            "business_size": "small",
            "business_model": "b2c",
            "team_size": 4,
            "founded_year": 2019,
            "vat_registered": True,
            "vat_number": "GB987654321",
        },
    )
    seasons = (
        ("Christmas parties", (12, 1), (12, 24), 30, "Party hair, nails and gift sets."),
        ("January lull", (1, 6), (2, 14), -22, "Everyone is saving after Christmas."),
        ("Wedding season", (5, 15), (8, 31), 8, "Brides and guests book colour and styling."),
    )
    for name, start, end, pct, notes in seasons:
        owner.call(
            "POST",
            f"{base}/seasons",
            json={
                "name": name,
                "start": {"month": start[0], "day": start[1]},
                "end": {"month": end[0], "day": end[1]},
                "expected_change_pct": pct,
                "notes": notes,
            },
        )
    goals = (
        ("Keep 60% of clients coming back", "improve_retention", "customer_retention_pct", "percent", "60", "50", 1),
        ("Cut missed appointments below 5%", "improve_margin", "no_show_rate_pct", "percent", "5", "9", 2),
        ("Fill more of the team's time", "improve_margin", "utilisation_pct", "percent", "70", "55", 3),
    )  # fmt: skip
    for title, goal_type, kpi, unit, target, baseline, priority in goals:
        owner.call(
            "POST",
            f"{base}/goals",
            json={"title": title, "goal_type": goal_type, "kpi_code": kpi, "target_unit": unit,
                  "target_value": target, "baseline_value": baseline, "priority": priority,
                  "target_date": "2027-03-31"},
        )  # fmt: skip
    owner.call(
        "PATCH",
        f"{base}/settings",
        json={"week_start_day": 2, "quiet_hours": {"start": "21:00:00", "end": "07:00:00"}},
    )


def _lists(db: Session) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    rows = [
        ("sales_channel", "In the salon", None),
        ("sales_channel", "Online booking", None),
        ("customer_type", "Regular client", None),
        ("customer_type", "Occasional", None),
        ("cost_category", "Rent", False),
        ("cost_category", "Wages", False),
        ("cost_category", "Utilities", False),
        ("cost_category", "Insurance", False),
        ("cost_category", "Software", False),
        ("cost_category", "Marketing", False),
        ("cost_category", "Salon supplies", True),
    ]
    for kind, name, cost_of_sales in rows:
        item = BusinessListItem(kind=kind, name=name, is_cost_of_sales=cost_of_sales)
        db.add(item)
        db.flush()
        ids[name] = item.id
    return ids


# --- trading -------------------------------------------------------------------------------------


class _Person:
    def __init__(self, customer: Customer, joined: date, every: int, favourite: str, regular: bool):
        self.customer, self.joined, self.every = customer, joined, every
        self.favourite, self.regular = favourite, regular
        self.last: date | None = None
        self.leaves: date | None = None


def _people(db: Session, rng: random.Random) -> list[_Person]:
    """About 500 clients over the year: some regulars who come every few weeks, some who come now and
    then, new ones joining each month, and a few regulars who stop coming."""
    people: list[_Person] = []
    names: set[str] = set()
    service_names = [s[0] for s in SERVICES]
    for index in range(500):
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in names:
                names.add(name)
                break
        regular = index < 260
        if index < 330:
            joined = START - timedelta(days=rng.randint(30, 400))  # already clients
        else:
            month_offset = (index - 330) // 14
            joined = START + timedelta(days=int(month_offset * 30.4) + rng.randint(0, 28))
        customer = Customer(
            name=name,
            email=f"{name.lower().replace(' ', '.')}@{EMAIL_DOMAIN}",
            postcode=f"BN{rng.randint(1, 3)} {rng.randint(1, 9)}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}",
        )
        db.add(customer)
        favourite = rng.choice(service_names[:5] + service_names[5:] if regular else service_names)
        every = rng.randint(24, 40) if regular else rng.randint(60, 110)
        person = _Person(customer, joined, every, favourite, regular)
        if regular and index % 29 == 5 and index < 330:  # these stop coming
            person.leaves = START + timedelta(days=rng.randint(120, 330))
        people.append(person)
    db.flush()
    return people


def _price(service: str) -> tuple[D, int, float, tuple[str, ...]]:
    for name, price, minutes, cost, who in SERVICES:
        if name == service:
            return D(price), minutes, cost, who
    raise KeyError(service)


def _status(day: date, rng: random.Random) -> str:
    """Mostly kept. Missed appointments creep up from August, which is something to act on."""
    cancel = 0.10 if day >= date(2026, 9, 1) else 0.08
    miss = 0.075 if day >= date(2026, 8, 1) else 0.035
    roll = rng.random()
    if roll < miss:
        return "no_show"
    if roll < miss + cancel:
        return "cancelled"
    return "completed"


def _rating(day: date, rng: random.Random) -> int | None:
    if rng.random() > 0.42:
        return None
    worse = day >= date(2026, 8, 15)
    weights = (2, 4, 14, 30, 50) if not worse else (4, 7, 20, 30, 39)
    return rng.choices((1, 2, 3, 4, 5), weights)[0]


def _trading(db: Session, org_ids: dict[str, uuid.UUID], rng: random.Random, say) -> dict:
    say("  clients, appointments, sales and stock")
    people = _people(db, rng)
    suppliers = {
        n: Supplier(name=n)
        for n in (
            "Brighton Beauty Wholesale",
            "Sussex Salon Supplies",
            "South Coast Energy",
            "Marsh Landlords",
        )
    }
    db.add_all(suppliers.values())
    products = []
    for name, sku, price, cost, _opening, _delivery in PRODUCTS:
        p = Product(
            name=name, sku=sku, unit_price_ex_vat=D(price), unit_cost=D(cost), vat_rate=D(20)
        )
        db.add(p)
        products.append(p)
    db.flush()
    stock = {p.id: D(0) for p in products}
    for p, (_n, _s, _p, _c, opening, _d) in zip(products, PRODUCTS, strict=True):
        db.add(
            StockMovement(
                product_id=p.id,
                moved_on=START,
                kind="opening",
                quantity=D(opening),
                unit_cost=p.unit_cost,
            )
        )
        stock[p.id] += opening
    channels = (org_ids["In the salon"], org_ids["Online booking"])

    totals = {"sales": D(0), "kept": 0, "all": 0}
    day = START
    while day <= END:
        # closed Sunday and Monday
        if day.weekday() in (6, 0):
            day += timedelta(days=1)
            continue
        # deliveries on the first Tuesday of each month
        if day.day <= 7 and day.weekday() == 1:
            for p, (_n, _s, _p, _c, _o, delivery) in zip(products, PRODUCTS, strict=True):
                if delivery:
                    db.add(
                        StockMovement(
                            product_id=p.id,
                            moved_on=day,
                            kind="delivery",
                            quantity=D(delivery),
                            unit_cost=p.unit_cost,
                        )
                    )
                    stock[p.id] += delivery
        busy = (
            BUSY[day.month]
            * (1.25 if day.weekday() == 5 else 1.0)
            * (0.85 if day.weekday() == 1 else 1.0)
        )
        want = max(4, round(14 * busy) + rng.choice((-1, 0, 0, 1)))
        eligible = [p for p in people if p.joined <= day and (p.leaves is None or p.leaves > day)]
        due = [
            p
            for p in eligible
            if p.last is None or (day - p.last).days >= p.every * rng.uniform(0.8, 1.15)
        ]
        rng.shuffle(due)
        chosen = due[:want]
        while len(chosen) < want:  # a few walk-ins who are not on the books
            chosen.append(None)
        for person in chosen:
            service = person.favourite if person and rng.random() < 0.8 else rng.choice(SERVICES)[0]
            price, planned, cost_share, who = _price(service)
            staff = rng.choice(who)
            status = _status(day, rng)
            taken = None
            if status == "completed" and rng.random() < 0.92:
                stretch = 1.12 if service in ("Colour", "Highlights") else 1.0
                taken = max(10, round(planned * rng.uniform(0.82, 1.28) * stretch))
            cancelled_on = None
            if status == "cancelled":
                cancelled_on = day - timedelta(days=rng.choice((0, 1, 1, 2, 3, 5, 7)))
            booking = Booking(
                booked_for=day,
                service=service,
                status=status,
                customer_id=person.customer.id if person else None,
                staff=staff,
                cancelled_on=cancelled_on,
                rating=_rating(day, rng) if status == "completed" else None,
                minutes_planned=planned,
                minutes_taken=taken,
            )
            db.add(booking)
            totals["all"] += 1
            if status != "completed":
                continue
            totals["kept"] += 1
            if person:
                person.last = day
            net = price
            lines = [("service", service, price, price * D(str(cost_share)), None)]
            for p, (_n, _s, pprice, pcost, _o, _d) in zip(products, PRODUCTS, strict=True):
                if rng.random() < 0.025 and stock[p.id] > 0:
                    lines.append(("product", p.name, D(pprice), D(pcost), p))
                    net += D(pprice)
                    stock[p.id] -= 1
                    break
            net = _money(net)
            vat = _money(net * D("0.2"))
            sale = Sale(
                kind="sale", sold_on=day, customer_id=person.customer.id if person else None,
                sales_channel_id=rng.choices(channels, (80, 20))[0],
                net_amount=net, vat_amount=vat, gross_amount=net + vat,
            )  # fmt: skip
            db.add(sale)
            db.flush()
            for _kind, description, line_net, line_cost, product in lines:
                line = SaleLine(
                    sale_id=sale.id, product_id=product.id if product else None,
                    description=description, quantity=D(1), unit_price_ex_vat=line_net,
                    net_amount=_money(line_net), vat_rate=D(20), vat_amount=_money(line_net * D("0.2")),
                    cost_amount=_money(line_cost),
                )  # fmt: skip
                db.add(line)
                db.flush()
                if product is not None:
                    db.add(
                        StockMovement(
                            product_id=product.id,
                            moved_on=day,
                            kind="sale",
                            quantity=D(-1),
                            unit_cost=product.unit_cost,
                            sale_line_id=line.id,
                        )
                    )
            totals["sales"] += net
        day += timedelta(days=1)

    # appointments still to come, so rebooking has something to count: most regulars who came lately
    recent = END - timedelta(days=45)
    for person in [
        p for p in people if p.regular and p.leaves is None and p.last and p.last >= recent
    ]:
        if rng.random() > 0.62:
            continue
        later = TODAY + timedelta(days=rng.randint(1, 30))
        while later.weekday() in (6, 0):
            later += timedelta(days=1)
        service = person.favourite
        _price_, planned, _c, who = _price(service)
        db.add(
            Booking(
                booked_for=later,
                service=service,
                status="booked",
                customer_id=person.customer.id,
                staff=rng.choice(who),
                minutes_planned=planned,
            )
        )
    db.flush()
    return {"people": people, "suppliers": suppliers, "totals": totals, "products": products}


def _invoices(db: Session, people: list[_Person], rng: random.Random) -> None:
    """Wedding parties and company treatments that are invoiced rather than paid at the till."""
    kinds = (
        ("Wedding party hair and make-up", 380, 1150),
        ("Staff wellbeing day", 260, 700),
        ("Prom and party styling", 220, 520),
    )
    when = [START + timedelta(days=rng.randint(10, 360)) for _ in range(30)]
    for index, sold_on in enumerate(sorted(when)):
        description, low, high = rng.choice(kinds)
        net = _money(rng.randint(low, high))
        vat = _money(net * D("0.2"))
        due = sold_on + timedelta(days=14)
        recent = sold_on > END - timedelta(days=45)
        paid = None
        if not recent and not (index % 9 == 4):  # a couple of older ones are still unpaid
            paid = sold_on + timedelta(days=rng.randint(8, 38))
        elif recent and rng.random() < 0.4:
            paid = min(END, sold_on + timedelta(days=rng.randint(6, 12)))
        customer = rng.choice([p for p in people if p.joined <= sold_on])
        sale = Sale(
            kind="sale", sold_on=sold_on, customer_id=customer.customer.id,
            net_amount=net, vat_amount=vat, gross_amount=net + vat, due_on=due, paid_on=paid,
            notes=description,
        )  # fmt: skip
        db.add(sale)
        db.flush()
        db.add(
            SaleLine(
                sale_id=sale.id, description=description, quantity=D(1), unit_price_ex_vat=net,
                net_amount=net, vat_rate=D(20), vat_amount=vat, cost_amount=_money(net * D("0.12")),
            )
        )  # fmt: skip
    db.flush()


def _spend(db, ids, suppliers, month, day, category, net, vat_rate, description, supplier=None):
    last = monthrange(month.year, month.month)[1]
    net_d = _money(net)
    vat = _money(net_d * D(vat_rate) / D(100))
    db.add(
        Expense(
            spent_on=date(month.year, month.month, min(day, last)),
            cost_category_id=ids[category],
            supplier_id=suppliers[supplier].id if supplier else None,
            description=description, net_amount=net_d, vat_amount=vat, gross_amount=net_d + vat,
        )
    )  # fmt: skip


def _costs(
    db: Session, ids: dict[str, uuid.UUID], suppliers: dict[str, Supplier], rng: random.Random
) -> None:
    month = date(START.year, START.month, 1)
    while month <= END:

        def spend(day, category, net, vat_rate, description, supplier=None, month=month):
            _spend(db, ids, suppliers, month, day, category, net, vat_rate, description, supplier)

        quiet = BUSY[month.month]
        spend(1, "Rent", 1900, 0, "Salon rent", "Marsh Landlords")
        spend(28, "Wages", 6900 + (400 if month.month == 12 else 0), 0, "Team wages")
        spend(
            12,
            "Utilities",
            300 + rng.randint(-30, 60),
            20,
            "Electricity and water",
            "South Coast Energy",
        )
        spend(3, "Insurance", 95, 0, "Salon insurance")
        spend(5, "Software", 140, 20, "Booking and till software")
        ad = 420 + (360 if month.month in (9, 10) else 0)  # more spent on Instagram adverts lately
        spend(8, "Marketing", ad, 20, "Instagram and Facebook adverts")
        for week in (4, 11, 18, 25):
            spend(
                week,
                "Salon supplies",
                350 * quiet + rng.randint(-40, 60),
                20,
                "Colour and care products",
                "Brighton Beauty Wholesale",
            )
        if month.month in (12, 3):
            spend(
                16,
                "Salon supplies",
                600,
                20,
                "Stock for gift sets and parties",
                "Sussex Salon Supplies",
            )
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    db.flush()


def _hours(db: Session, rng: random.Random) -> None:
    day = START
    while day <= END:
        if day.weekday() not in (6, 0):
            for name in STAFF:
                if name == "Dev" and day.weekday() not in (4, 5):
                    continue
                if rng.random() < 0.04:  # a day off
                    continue
                hours = D("6.5") if day.weekday() != 5 else D("7")
                db.add(StaffHours(worked_on=day, staff=name, hours=hours))
        day += timedelta(days=1)
    db.flush()


def _website(db: Session, rng: random.Random) -> None:
    channels = (
        ("Organic Search", 68),
        ("Social", 38),
        ("Direct", 28),
        ("Email", 12),
        ("Referral", 8),
    )
    day = START
    while day <= END:
        growth = 1 + (day - START).days * 0.0012
        busy = BUSY[day.month] * (1.15 if day.weekday() in (5, 6) else 1.0)
        for channel, base in channels:
            sessions = max(1, round(rng.gauss(base * growth * busy, base * 0.12)))
            users = max(1, round(sessions * rng.uniform(0.8, 0.92)))
            new = max(0, round(users * rng.uniform(0.5, 0.7)))
            events = round(
                sessions
                * (0.045 if channel in ("Email", "Social") else 0.03)
                * rng.uniform(0.6, 1.4)
            )
            db.add(
                WebVisit(
                    day=day,
                    channel=channel,
                    sessions=sessions,
                    users=users,
                    new_users=new,
                    key_events=events,
                )
            )
        day += timedelta(days=1)
    db.flush()


def _adverts(db: Session, rng: random.Random) -> None:
    """What was spent on Facebook and Instagram adverts each day, with the clicks and enquiries
    they brought. Spending rises in September and October, which the marketing figures show."""
    day = START
    while day <= END:
        step = 1.0 + (0.45 if day >= date(2026, 9, 1) else 0.0)
        for platform, base, per_click in (("Facebook", 7.5, 0.62), ("Instagram", 6.0, 0.48)):
            spend = _money(max(1, rng.gauss(base * step, base * 0.1)))
            clicks = round(float(spend) / per_click * rng.uniform(0.9, 1.1))
            results = round(clicks * rng.uniform(0.03, 0.06))
            db.add(
                AdSpend(
                    day=day, platform=platform, spend=spend, impressions=clicks * rng.randint(38, 55),
                    clicks=clicks, results=results,
                )
            )  # fmt: skip
        day += timedelta(days=1)
    db.flush()


def _bank(db: Session, rng: random.Random) -> None:
    """Card takings paid in each open day, and the usual payments out."""
    takings: dict[date, D] = {}
    for sale in db.scalars(select(Sale).where(Sale.due_on.is_(None))):
        takings[sale.sold_on] = takings.get(sale.sold_on, D(0)) + sale.gross_amount
    for day, amount in sorted(takings.items()):
        db.add(
            BankTransaction(
                day=day + timedelta(days=1),
                amount=_money(amount * D("0.985")),
                description="Card takings",
                counterparty="Card processor",
                account_name="Business current account",
            )
        )
    for sale in db.scalars(select(Sale).where(Sale.paid_on.is_not(None))):
        db.add(
            BankTransaction(
                day=sale.paid_on,
                amount=sale.gross_amount,
                description="Invoice paid",
                counterparty="Customer",
                account_name="Business current account",
            )
        )
    for expense in db.scalars(select(Expense)):
        db.add(
            BankTransaction(
                day=expense.spent_on,
                amount=-expense.gross_amount,
                description=expense.description,
                counterparty=None,
                account_name="Business current account",
            )
        )
    db.flush()


def _leads(db: Session, people: list[_Person], rng: random.Random) -> None:
    """Enquiries for weddings, parties and company packages, and clients renewing a package."""
    month = date(START.year, START.month, 1)
    names = (
        "Hughes",
        "Okafor",
        "Brightwater",
        "Marlow",
        "Singh",
        "Patel",
        "Rowe",
        "Dunmore",
        "Ellis",
        "Kerr",
    )
    while month <= END:
        for _ in range(rng.randint(3, 5)):
            created = month + timedelta(days=rng.randint(0, 24))
            kind = "renewal" if rng.random() < 0.18 else "new"
            title = (
                f"Monthly treatment package: {rng.choice(names)}"
                if kind == "renewal"
                else f"{rng.choice(('Wedding party', 'Prom party', 'Staff day', 'Hen party'))}: {rng.choice(names)}"
            )
            value = _money(rng.randint(250, 1500))
            age = (END - created).days
            roll = rng.random()
            if age < 18:
                stage = "lead" if roll < 0.55 else "proposal"
            else:
                stage = "won" if roll < 0.45 else "lost" if roll < 0.75 else "proposal"
            proposal_on = created + timedelta(days=rng.randint(1, 6)) if stage != "lead" else None
            closed_on = None
            if stage in ("won", "lost"):
                closed_on = min(END, (proposal_on or created) + timedelta(days=rng.randint(3, 21)))
            customer = rng.choice([p for p in people if p.joined <= created])
            db.add(
                Opportunity(
                    name=title,
                    stage=stage,
                    kind=kind,
                    customer_id=customer.customer.id,
                    value=value,
                    created_on=created,
                    proposal_on=proposal_on,
                    closed_on=closed_on,
                )
            )
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    db.flush()


# --- the whole thing ------------------------------------------------------------------------------


def run(say: Callable[[str], None] = print) -> uuid.UUID:
    _need_dev()
    passwords = _passwords()
    sessions = get_sessionmaker()
    with sessions() as db:
        if _find(db) is not None:
            raise showcase.ShowcaseError(
                f'"{NAME}" already exists. Run `unsalon` first to build it again.'
            )
    client = TestClient(create_app())
    owner = showcase.Api(client, showcase.OWNER_EMAIL, passwords["owner"])
    with sessions() as db:
        org_id = _create(owner, db, say)
    with sessions() as db:
        showcase._plan(db, org_id, say)
    _profile(owner, org_id, say)
    rng = random.Random(20261115)
    with sessions() as db:
        with tenant_scope(db, org_id):
            ids = _lists(db)
            trading = _trading(db, ids, rng, say)
            say(
                "  invoices, costs, the team's hours, website visits, adverts, bank movements and leads"
            )
            _invoices(db, trading["people"], rng)
            _costs(db, ids, trading["suppliers"], rng)
            _hours(db, rng)
            _website(db, rng)
            _adverts(db, rng)
            _bank(db, rng)
            _leads(db, trading["people"], rng)
            db.commit()
        totals = trading["totals"]
        say(
            f"  {totals['all']:,} appointments, {totals['kept']:,} kept, sales before VAT £{totals['sales']:,.0f}"
        )
    _alert_choices(owner, org_id, say)
    showcase._finish_onboarding(owner, org_id, say)
    with sessions() as db:
        _analytics(owner, db, org_id, passwords["manager"], say)
    say(f"Done. Open {NAME} in the browser.")
    return org_id


def _analytics(
    owner: "showcase.Api", db: Session, org_id: uuid.UUID, manager_password: str, say
) -> None:
    """Key figures, health, changes, explanations, forecasts, actions, alerts and reports. Like the
    showcase's, but suggestions are made only for the latest big problems, so Today shows a short,
    believable list rather than every move of the year."""
    from app.core.errors import NotFoundError
    from app.services import alerts, detection, forecast, health, kpi, memory
    from app.services.jobs import JobTenant

    say("  key figures, health, changes, explanations and forecasts")
    base = f"/organizations/{org_id}"
    user = db.scalars(
        select(User).where(User.email == showcase.OWNER_EMAIL).execution_options(**ACROSS_TENANTS)
    ).one()
    tenant = JobTenant(organization_id=org_id, user=user)
    with tenant_scope(db, org_id):
        kpi.calculate(db, tenant, granularity="month", trigger="manual")
        health.calculate(db, tenant)
        detection.detect(db, tenant)
        memory.rebuild(db, tenant)
        for code in forecast.forecastable():
            for month in range(5, 11):
                try:
                    forecast.calculate(db, tenant, code, 3, as_of=date(2026, month, 1))
                except NotFoundError:
                    break
        forecast.calculate_all(db, tenant)
    events = owner.call("GET", f"{base}/changes?limit=300")
    latest = max(e["period_start"] for e in events)
    recent = [e for e in events if e["period_start"] >= "2026-08-01"]
    for kind in ("material_change", "anomaly"):
        for event in [e for e in recent if e["kind"] == kind and e["severity"] == "major"][:6]:
            owner.call("POST", f"{base}/changes/{event['id']}/diagnosis")
    bad = [e for e in recent if e["effect"] == "bad" and e["kind"] == "material_change"][:9]
    for event in bad:
        if event["status"] != "diagnosed":
            owner.call("POST", f"{base}/changes/{event['id']}/diagnosis")
        owner.call("POST", f"{base}/changes/{event['id']}/recommendation")
    # The oldest of them can be followed up, so the actions come in every state
    showcase._actions(owner, db, org_id, bad, manager_password, say)
    say(f"  alerts for the things that need attention (latest month {latest})")
    with tenant_scope(db, org_id):
        alerts.evaluate(db, tenant)
    say("  written reports and a weekly schedule")
    reports = owner.call("GET", f"{base}/reports")
    for report in reports:
        owner.call("POST", f"{base}/reports/{report['id']}/generate")
    if not owner.call("GET", f"{base}/reports/schedules"):
        members = owner.call("GET", f"{base}/members")
        monthly = next(r for r in reports if r["kind"] == "monthly")
        owner.call(
            "POST",
            f"{base}/reports/{monthly['id']}/schedules",
            json={"frequency": "weekly", "weekday": 1,
                  "recipients": [m["user_id"] for m in members if m["role"] in ("owner", "manager")]},
        )  # fmt: skip


def _alert_choices(owner: "showcase.Api", org_id: uuid.UUID, say: Callable[[str], None]) -> None:
    """What the owner chose: only be told about big changes, so the list stays short."""
    say("  the owner chooses to hear about big changes only")
    base = f"/organizations/{org_id}/alerts/rules"
    for rule in owner.call("GET", base):
        if "min_size" in rule["params"]:
            owner.call(
                "PUT",
                f"{base}/{rule['code']}",
                json={
                    "enabled": rule["enabled"],
                    "severity": rule["severity"],
                    "params": {**rule["params"], "min_size": "major"},
                },
            )


def remove(say: Callable[[str], None] = print) -> None:
    """Take the salon out again (everything in it goes with it)."""
    _need_dev()
    with get_sessionmaker()() as db:
        org = _find(db)
        if org is None:
            say("There is no demo salon.")
            return
        if not org.name.rstrip().endswith("(demo data)"):
            raise showcase.ShowcaseError("Refusing: not a demo business.")
        db.execute(
            delete(Organization).where(Organization.id == org.id)
        )  # the database removes the rest
        db.commit()
    say("The demo salon is gone.")
