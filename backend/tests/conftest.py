import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.services.email import EmailMessage, get_email_sender
from app.services.storage import LocalFileStorage, get_file_storage
from tests.shops import bakery, costs  # noqa: F401  (fixtures shared by several test files)

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "VYTERLIX_TEST_DATABASE_URL",
    "postgresql+psycopg://vyterlix:vyterlix@localhost:5432/vyterlix_test",
)


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", _env_file=None)


def _no_real_database():
    raise RuntimeError(
        "Test tried to use the real database. Request the `api` or `db` fixture instead."
    )
    yield  # pragma: no cover


def _no_real_upload_folder():
    raise RuntimeError("Test tried to use the real upload folder. Request `api` or `storage`.")


@pytest.fixture
def app(settings):
    app = create_app(settings)
    # Guard: tests must never reach the dev database configured in backend/.env.
    app.dependency_overrides[get_db] = _no_real_database
    app.dependency_overrides[get_file_storage] = _no_real_upload_folder
    return app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="session")
def db_engine():
    """Migrated test database. Runs real Alembic migrations, so migrations are tested too."""
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.fail(
            f"Test database unreachable ({TEST_DATABASE_URL}). Create it with:\n"
            '  psql -U postgres -c "CREATE DATABASE vyterlix_test OWNER vyterlix;"\n'
            f"Original error: {exc.orig}",
            pytrace=False,
        )

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine):
    """Session inside a transaction that is always rolled back — tests never leave data behind."""
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


class Outbox(list):
    """Collects emails instead of sending them."""

    def send(self, message: EmailMessage) -> None:
        self.append(message)


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:
    """Uploaded files go to a temporary folder, deleted after the test run."""
    return LocalFileStorage(tmp_path / "uploads")


@pytest.fixture
def api(app, db, outbox, storage) -> TestClient:
    """HTTP client using the rolled-back test session, test outbox and temp upload folder."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_email_sender] = lambda: outbox
    app.dependency_overrides[get_file_storage] = lambda: storage
    # https so the Secure refresh cookie round-trips like it does in a real browser.
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


TEST_PASSWORD = "correct horse battery"


@pytest.fixture
def signup(api, db, outbox):
    """Register + log in a user; returns their Authorization header.

    `verified=True` (default) marks the email verified, as most features require it.
    """
    from sqlalchemy import func, update

    from app.models.identity import User

    def _signup(email: str = "owner@acme.co.uk", *, verified: bool = True) -> dict[str, str]:
        res = api.post(
            "/api/v1/auth/register",
            json={"email": email, "password": TEST_PASSWORD, "full_name": email.split("@")[0]},
        )
        assert res.status_code == 201, res.text
        if verified:
            db.execute(update(User).where(User.email == email).values(email_verified_at=func.now()))
        res = api.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
        assert res.status_code == 200, res.text
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    return _signup


@pytest.fixture
def business(api, db, signup):
    """Two businesses (org ids) and logins: owner/viewer of Acme, owner of Rival."""
    import uuid

    from sqlalchemy import select

    from app.models.identity import OrganizationUser, Role, User

    ORGS = "/api/v1/organizations"
    auth = {
        "owner": signup("owner@acme.co.uk"),
        "viewer": signup("viewer@acme.co.uk"),
        "other": signup("owner@rival.co.uk"),
    }
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    other_org = api.post(ORGS, json={"name": "Rival"}, headers=auth["other"]).json()["id"]
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "viewer")).first(),
        )
    )
    db.flush()
    return org_id, other_org, auth
