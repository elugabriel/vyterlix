import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db.session import get_db


def test_liveness(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_readiness_reports_db_outage_with_error_envelope(app, client):
    class BrokenSession:
        def execute(self, *_):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    res = client.get("/api/v1/health/ready")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "database_unavailable"


def test_unknown_route_uses_error_envelope(client):
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    err = res.json()["error"]
    assert err["code"] == "not_found"
    assert err["request_id"] == res.headers["x-request-id"]


def test_validation_error_envelope(app, client):
    class Payload(BaseModel):
        amount: int

    @app.post("/_test/validate")
    def _validate(payload: Payload):
        return payload

    res = client.post("/_test/validate", json={"amount": "not-a-number"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"
    assert res.json()["error"]["details"][0]["loc"] == ["body", "amount"]


def test_unhandled_exception_hides_internals(app, client):
    @app.get("/_test/boom")
    def _boom():
        raise RuntimeError("secret internal detail")

    res = client.get("/_test/boom")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "internal_error"
    assert "secret" not in res.text
    assert res.json()["error"]["request_id"]


def test_request_id_is_echoed_when_safe_and_replaced_when_not(client):
    assert (
        client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )
    replaced = client.get("/api/v1/health", headers={"X-Request-ID": "bad id\nforged"})
    assert replaced.headers["x-request-id"] != "bad id\nforged"


def test_cors_allows_configured_origin_only(client):
    preflight = {"Access-Control-Request-Method": "GET"}
    ok = client.options("/api/v1/health", headers={"Origin": "http://localhost:5500", **preflight})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5500"

    bad = client.options("/api/v1/health", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in bad.headers


def test_wildcard_cors_rejected_in_prod():
    with pytest.raises(ValidationError):
        Settings(env="prod", cors_origins=["*"], _env_file=None)
