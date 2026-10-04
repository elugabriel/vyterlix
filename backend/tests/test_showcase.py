"""The demo-information seeder: it must be safe, and its planted mistakes must really be caught."""

import io
import uuid
from types import SimpleNamespace

import pytest

from app.demo import showcase
from app.models.identity import Organization
from tests.test_health import ORGS


@pytest.mark.parametrize("env", ["test", "staging", "prod"])
def test_it_refuses_to_run_anywhere_but_a_development_system(monkeypatch, env):
    monkeypatch.setattr(showcase, "get_settings", lambda: SimpleNamespace(env=env))
    with pytest.raises(showcase.ShowcaseError, match="development"):
        showcase.run([uuid.uuid4()])
    with pytest.raises(showcase.ShowcaseError, match="development"):
        showcase.remove()


def test_it_only_goes_into_a_business_named_as_demo_data(db, business):
    org_id = uuid.UUID(business[0])
    with pytest.raises(showcase.ShowcaseError, match="not a demo business"):
        showcase._org(db, org_id)
    org = db.get(Organization, org_id)
    org.name = "Acme Ltd (demo data)"
    db.flush()
    assert showcase._org(db, org_id).name == "Acme Ltd (demo data)"
    with pytest.raises(showcase.ShowcaseError, match="No business"):
        showcase._org(db, uuid.uuid4())


def test_every_demo_benchmark_says_it_is_demo_and_not_a_real_source():
    assert showcase.BENCHMARK_SOURCE.startswith("DEMO DATA")
    assert "invented" in showcase.BENCHMARK_SOURCE


def test_the_demo_team_lives_on_an_address_that_can_never_receive_mail():
    assert showcase.EMAIL_DOMAIN.endswith(".example")
    assert all(
        email.endswith("." + showcase.EMAIL_DOMAIN.split(".")[-1]) for _, email, _ in showcase.TEAM
    )


def upload(api, business, filename, body):
    base = f"{ORGS}/{business[0]}/imports"
    headers = business[2]["owner"]
    made = api.post(
        base,
        data={"dataset": "sales"},
        files={"file": (filename, io.BytesIO(body.encode("utf-8-sig")), "text/csv")},
        headers=headers,
    )
    assert made.status_code == 201, made.text
    ident = made.json()["id"]
    suggested = api.get(f"{base}/{ident}/mapping", headers=headers).json()["suggested_mapping"]
    saved = api.put(
        f"{base}/{ident}/mapping",
        json={"mapping": suggested, "options": {"vat_inclusive": True}},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    return api.post(f"{base}/{ident}/validate", headers=headers).json()


def test_the_file_with_mistakes_really_has_the_mistakes_it_promises(api, business):
    result = upload(api, business, "demo-sales-with-mistakes.csv", showcase.MISTAKES_CSV)
    # Two good rows, a date that does not exist, a comma used for pence, and a row sent twice
    assert (result["valid"], result["invalid"], result["duplicate"]) == (3, 2, 1)


def test_the_small_file_is_clean_so_it_can_be_imported_and_then_undone(api, business):
    result = upload(api, business, "demo-sales-small-undone.csv", showcase.SMALL_CSV)
    assert (result["valid"], result["invalid"], result["duplicate"]) == (2, 0, 0)
