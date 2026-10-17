# ruff: noqa: E501, F811
"""When we cannot suggest anything, the card says what is missing (client feedback §6)."""

from datetime import date

import pytest

from tests.test_alerts import alerts_of, evaluate
from tests.test_detection import Values, run
from tests.test_health import ORGS


def card_for(api, business, code):
    evaluate(api, business)
    for alert in alerts_of(api, business):
        res = api.get(
            f"{ORGS}/{business[0]}/alerts/{alert['id']}/card", headers=business[2]["owner"]
        )
        if res.status_code == 200 and (res.json().get("chart") or {}).get("code") == code:
            return res.json()
    raise AssertionError(f"no alert about {code}")


@pytest.fixture
def worse(db, business):
    values = Values(db, business)
    values.put("average_customer_value", date(2026, 2, 1), 500, None)
    values.put("average_customer_value", date(2026, 3, 1), 300, 500)
    run(db, business)
    return business


def test_with_no_suggestion_the_card_says_what_would_help(api, worse):
    card = card_for(api, worse, "average_customer_value")
    assert card["state"] == "no_suggestion" and card["suggestion"] is None
    assert any("which customer bought" in m for m in card["missing"])


def test_a_card_with_a_suggestion_has_nothing_missing(api, db, business):
    values = Values(db, business)
    values.put("customer_retention_pct", date(2026, 2, 1), 38, None)
    values.put("customer_retention_pct", date(2026, 3, 1), 26, 38)
    run(db, business)
    card = card_for(api, business, "customer_retention_pct")
    assert card["state"] == "open" and card["missing"] == []


def test_poor_data_is_one_of_the_things_said(api, db, business):
    values = Values(db, business)
    values.put("average_customer_value", date(2026, 2, 1), 500, None, quality=40)
    values.put("average_customer_value", date(2026, 3, 1), 300, 500, quality=40)
    run(db, business)
    card = card_for(api, business, "average_customer_value")
    assert any("are missing, so we are less sure" in m for m in card["missing"])
