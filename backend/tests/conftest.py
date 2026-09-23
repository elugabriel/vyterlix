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


@pytest.fixture
def app(settings):
    app = create_app(settings)
    # Guard: tests must never reach the dev database configured in backend/.env.
    app.dependency_overrides[get_db] = _no_real_database
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


@pytest.fixture
def api(app, db) -> TestClient:
    """HTTP client whose requests use the rolled-back test session."""
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)
