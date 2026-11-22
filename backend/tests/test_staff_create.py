# ruff: noqa: E501
"""The very first admin: a verified staff account made from the command line on a site with no accounts."""

import pytest
from sqlalchemy import select

from app.cli import staff as cli
from app.core.errors import AppError, ConflictError
from app.models.admin import PlatformStaff
from app.models.identity import AuditLog, User
from app.services import admin as service

EMAIL = "first.admin@client.example"


@pytest.fixture(autouse=True)
def test_database(db, monkeypatch):
    """The command opens its own session; point it at the test database, never the real one."""

    class Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "get_sessionmaker", lambda: lambda: Session())


PASSWORD = "a long first password"


def login(api, email=EMAIL, password=PASSWORD):
    return api.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_the_new_account_is_verified_staff_and_can_sign_in_and_open_the_admin_pages(api, db):
    user = service.create_staff_account(
        db, email=EMAIL.upper(), full_name="  First Admin ", password=PASSWORD, role="admin"
    )
    assert (
        user.email == EMAIL
        and user.full_name == "First Admin"
        and user.email_verified_at is not None
    )
    row = db.scalars(select(PlatformStaff).where(PlatformStaff.user_id == user.id)).one()
    assert (row.role, row.is_active) == ("admin", True)
    res = login(api)
    assert res.status_code == 200, res.text
    token = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert (
        api.get("/api/v1/admin/organizations", headers=token).status_code == 200
    )  # an empty list is fine


def test_the_account_and_its_staff_rights_are_written_to_the_audit_log(db):
    service.create_staff_account(
        db, email=EMAIL, full_name="First Admin", password=PASSWORD, role="admin"
    )
    actions = {a.action for a in db.scalars(select(AuditLog))}
    assert {"user.registered", "admin.staff_granted"} <= actions


def test_a_support_account_can_look_but_not_change(api, db):
    service.create_staff_account(
        db, email=EMAIL, full_name="Helper", password=PASSWORD, role="support"
    )
    token = {"Authorization": f"Bearer {login(api).json()['access_token']}"}
    assert api.get("/api/v1/admin/organizations", headers=token).status_code == 200


@pytest.mark.parametrize("password", ["short", "x" * 129, EMAIL, EMAIL.upper()])
def test_a_password_that_breaks_the_rules_is_refused_and_no_account_is_made(db, password):
    with pytest.raises(AppError) as e:
        service.create_staff_account(
            db, email=EMAIL, full_name="First Admin", password=password, role="admin"
        )
    assert e.value.code == "weak_password"
    assert db.scalars(select(User).where(User.email == EMAIL)).first() is None


def test_a_bad_email_or_no_name_is_refused(db):
    for email, name, code in (
        ("not-an-email", "A B", "bad_email"),
        ("a b@x.com", "A B", "bad_email"),
        (EMAIL, "   ", "name_needed"),
    ):
        with pytest.raises(AppError) as e:
            service.create_staff_account(
                db, email=email, full_name=name, password=PASSWORD, role="admin"
            )
        assert e.value.code == code


def test_an_email_that_already_has_an_account_is_not_taken_over(db):
    service.create_staff_account(
        db, email=EMAIL, full_name="First Admin", password=PASSWORD, role="admin"
    )
    with pytest.raises(ConflictError):
        service.create_staff_account(
            db,
            email=EMAIL,
            full_name="Someone Else",
            password="another long password",
            role="admin",
        )


def test_the_command_reads_the_password_from_the_environment_and_never_prints_it(
    api, db, monkeypatch, capsys
):
    monkeypatch.setenv("VYTERLIX_NEW_PASSWORD", PASSWORD)
    assert cli.main(["create", EMAIL, "--name", "First Admin"]) == 0
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err and EMAIL in out.out and "admin" in out.out
    assert login(api).status_code == 200


def test_the_command_asks_twice_and_refuses_if_the_two_differ(db, monkeypatch, capsys):
    monkeypatch.delenv("VYTERLIX_NEW_PASSWORD", raising=False)
    answers = iter([PASSWORD, "something different"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create", EMAIL, "--name", "First Admin"]) == 1
    assert "not the same" in capsys.readouterr().err
    assert db.scalars(select(User).where(User.email == EMAIL)).first() is None


def test_the_command_refuses_a_weak_password_in_plain_words(db, monkeypatch, capsys):
    monkeypatch.setenv("VYTERLIX_NEW_PASSWORD", "tooshort")
    assert cli.main(["create", EMAIL, "--name", "First Admin"]) == 1
    err = capsys.readouterr().err
    assert "12 to 128 characters" in err and "tooshort" not in err
