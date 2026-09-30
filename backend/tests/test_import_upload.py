"""Uploading data files: POST/GET/PATCH /organizations/{id}/imports."""

import io
import uuid
from datetime import UTC, datetime

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.session import get_db
from app.db.tenant import tenant_scope
from app.main import create_app
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.models.imports import DataImport
from app.services import imports as imports_service
from app.services.email import get_email_sender
from app.services.storage import get_file_storage

ORGS = "/api/v1/organizations"
CSV = "Date,Product,Total\n28/09/2026,Loaf,£4.50\n29/09/2026,Bun,£1.20\n".encode()


@pytest.fixture
def business(api, db, signup):
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


def url(org_id, suffix=""):
    return f"{ORGS}/{org_id}/imports{suffix}"


def upload(api, business, content=CSV, name="sales.csv", dataset="sales", who="owner", org=0):
    org_id = business[org]
    return api.post(
        url(org_id),
        files={"file": (name, content, "application/octet-stream")},
        data={"dataset": dataset},
        headers=business[2][who],
    )


def patch_import(api, business, import_id, body, who="owner"):
    return api.patch(url(business[0], f"/{import_id}"), json=body, headers=business[2][who])


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def stored_files(storage):
    return [p for p in storage.root.rglob("*") if p.is_file()] if storage.root.exists() else []


def count_imports(db, org_id):
    with tenant_scope(db, uuid.UUID(org_id)):
        return db.scalar(select(func.count()).select_from(DataImport))


# --- uploading a CSV ---------------------------------------------------------------------------


def test_upload_a_csv(api, business, storage):
    res = upload(api, business)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "uploaded"
    assert (body["dataset"], body["source"]) == ("sales", "csv")
    assert body["original_filename"] == "sales.csv"
    assert body["file_size_bytes"] == len(CSV)
    assert (body["row_count"], body["header_row"], body["sheet_name"]) == (2, 1, None)
    assert body["imported_count"] == 0 and body["file_deleted_at"] is None
    assert body["preview"]["headers"] == ["Date", "Product", "Total"]
    assert body["preview"]["sample_rows"][0] == ["28/09/2026", "Loaf", "£4.50"]
    assert body["preview"]["needs_sheet"] is False
    assert body["duplicate_of"] is None
    assert len(stored_files(storage)) == 1


def test_the_file_is_stored_under_a_generated_name(api, business, storage):
    res = upload(api, business, name="../../evil name.csv")
    assert res.status_code == 201
    assert (
        res.json()["original_filename"] == "evil name.csv"
    )  # folders stripped from the name shown
    org_id, import_id = business[0], res.json()["id"]
    [path] = stored_files(storage)
    assert path.relative_to(storage.root).as_posix() == f"{org_id}/{import_id}.csv"


def test_the_uploader_and_an_audit_entry_are_recorded(api, db, business):
    res = upload(api, business)
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(res.json()["id"]))
        assert imp.uploaded_by_user_id is not None
        assert len(imp.file_sha256) == 64
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "import.uploaded")).one()
    assert entry.organization_id == uuid.UUID(business[0])
    assert entry.target_id == res.json()["id"]
    assert entry.details["rows"] == 2 and entry.details["dataset"] == "sales"


@pytest.mark.parametrize(
    "dataset", ["sales", "expenses", "customers", "products", "stock_movements"]
)
def test_every_dataset_can_be_uploaded(api, business, dataset):
    assert upload(api, business, dataset=dataset).json()["dataset"] == dataset


def test_unknown_dataset_is_refused_and_nothing_is_stored(api, business, storage):
    assert upload(api, business, dataset="payroll").status_code == 422
    assert stored_files(storage) == []


def test_a_request_without_a_file_is_refused(api, business):
    res = api.post(url(business[0]), data={"dataset": "sales"}, headers=business[2]["owner"])
    assert res.status_code == 422


def test_csv_in_windows_1252_and_semicolons(api, business):
    text = "Date;Total\n28/09/2026;£4,50\n".encode("cp1252")
    body = upload(api, business, text).json()
    assert body["preview"]["encoding"] == "cp1252" and body["preview"]["delimiter"] == ";"
    assert body["preview"]["sample_rows"] == [["28/09/2026", "£4,50"]]


# --- uploading an Excel workbook ----------------------------------------------------------------


def test_upload_an_xlsx(api, business):
    book = xlsx({"Sales": [["Date", "Total"], ["28/09/2026", 4.5]]})
    body = upload(api, business, book, name="Sales 2026.XLSX").json()
    assert (body["source"], body["sheet_name"], body["row_count"]) == ("excel", "Sales", 1)
    assert body["preview"]["sheets"] == ["Sales"]


def test_a_workbook_with_several_sheets_asks_which_one(api, business):
    book = xlsx({"Notes": [["Note"], ["hi"]], "Sales": [["Date", "Total"], ["1/1", 5], ["2/1", 6]]})
    body = upload(api, business, book, name="book.xlsx").json()
    assert body["preview"]["needs_sheet"] is True
    assert body["preview"]["sheets"] == ["Notes", "Sales"]
    assert body["sheet_name"] is None and body["row_count"] == 0

    res = patch_import(api, business, body["id"], {"sheet_name": "Sales"}, "owner")
    assert res.status_code == 200, res.text
    assert res.json()["sheet_name"] == "Sales" and res.json()["row_count"] == 2
    assert res.json()["preview"]["headers"] == ["Date", "Total"]
    assert res.json()["preview"]["needs_sheet"] is False


# --- files that are refused ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("notes.txt", b"a,b\n1,2\n", "unsupported_file_type"),
        ("sales", b"a,b\n1,2\n", "unsupported_file_type"),
        ("old.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 50, "unsupported_file_type"),
        ("report.pdf", b"%PDF-1.7", "unsupported_file_type"),
        ("run.exe.csv", b"MZ\x90\x00" + b"\x00" * 20, "unsupported_file_type"),
        ("fake.xlsx", b"a,b\n1,2\n", "unsupported_file_type"),
        ("empty.csv", b"", "empty_file"),
        ("headings.csv", b"Date,Total\n", "no_data_rows"),
    ],
)
def test_bad_files_are_refused_and_leave_no_trace(api, db, business, storage, name, content, code):
    res = upload(api, business, content, name=name)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == code
    assert stored_files(storage) == []
    assert count_imports(db, business[0]) == 0
    assert db.scalar(select(func.count()).where(AuditLog.action == "import.uploaded")) == 0


def test_xls_advice_mentions_xlsx(api, business):
    res = upload(api, business, b"whatever", name="old.xls")
    assert ".xlsx" in res.json()["error"]["message"]


def test_too_many_rows_is_refused(api, business, storage, monkeypatch):
    monkeypatch.setattr(
        imports_service,
        "get_settings",
        lambda: Settings(env="test", _env_file=None, max_upload_rows=2),
    )
    assert upload(api, business, b"N\n1\n2\n3\n").json()["error"]["code"] == "too_many_rows"
    assert stored_files(storage) == []
    assert upload(api, business, b"N\n1\n2\n", name="ok.csv").status_code == 201


def test_a_file_over_the_size_limit_is_refused_and_removed(api, business, storage, monkeypatch):
    monkeypatch.setattr(
        imports_service,
        "get_settings",
        lambda: Settings(env="test", _env_file=None, max_upload_bytes=50),
    )
    res = upload(api, business, b"N\n" + b"1\n" * 100)
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "file_too_large"
    assert stored_files(storage) == []


def test_a_failure_saving_the_record_removes_the_file(api, business, storage, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(imports_service, "record_audit", boom)
    assert upload(api, business).status_code == 500
    assert stored_files(storage) == []


# --- request size limits (before the body is read) -----------------------------------------------


@pytest.fixture
def small_limit_client(db, outbox, storage):
    app = create_app(Settings(env="test", _env_file=None, max_upload_bytes=1000))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_email_sender] = lambda: outbox
    app.dependency_overrides[get_file_storage] = lambda: storage
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


def test_oversized_upload_is_refused_from_its_declared_length(small_limit_client):
    big = b"x" * (1024 * 1024 + 2000)
    res = small_limit_client.post(
        f"{ORGS}/{uuid.uuid4()}/imports",
        files={"file": ("big.csv", big)},
        data={"dataset": "sales"},
    )
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "file_too_large"
    assert "1 MB" not in res.json()["error"]["message"]  # names the real limit, not the overhead
    assert res.json()["error"]["request_id"] is not None


def test_oversized_upload_without_a_declared_length_is_cut_off(small_limit_client):
    def chunks():
        yield b'--zzz\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n\r\n'
        for _ in range(3):
            yield b"x" * (600 * 1024)

    res = small_limit_client.post(
        f"{ORGS}/{uuid.uuid4()}/imports",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=zzz"},
    )
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "file_too_large"


def test_ordinary_requests_are_limited_to_1_mb(api):
    res = api.post("/api/v1/auth/login", content=b'{"email":"' + b"a" * (1024 * 1024 + 10) + b'"}')
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "request_too_large"


def test_small_requests_are_unaffected(api):
    res = api.post("/api/v1/auth/login", json={"email": "nobody@acme.co.uk", "password": "x" * 12})
    assert res.status_code == 401


# --- permissions and separation between businesses ------------------------------


def test_only_people_who_may_manage_data_can_upload(api, business):
    assert upload(api, business, who="viewer").status_code == 403
    listed = api.get(url(business[0]), headers=business[2]["viewer"])
    assert listed.status_code == 403


def test_logging_in_is_required(api, business):
    res = api.post(url(business[0]), files={"file": ("a.csv", CSV)}, data={"dataset": "sales"})
    assert res.status_code == 401


def test_someone_from_another_business_cannot_upload_or_read(api, business):
    assert upload(api, business, who="other").status_code == 404  # not even revealed to exist
    made = upload(api, business).json()["id"]
    theirs = business[2]["other"]
    assert api.get(url(business[0]), headers=theirs).status_code == 404
    assert api.get(url(business[0], f"/{made}"), headers=theirs).status_code == 404


def test_an_import_id_from_another_business_is_not_found(api, business):
    made = upload(api, business).json()["id"]
    org_id, other_org, auth = business
    assert api.get(url(other_org, f"/{made}"), headers=auth["other"]).status_code == 404
    res = api.patch(url(other_org, f"/{made}"), json={"header_row": 2}, headers=auth["other"])
    assert res.status_code == 404


def test_each_business_sees_only_its_own_imports(api, business):
    upload(api, business, name="acme.csv")
    upload(api, business, name="rival.csv", who="other", org=1)
    acme = api.get(url(business[0]), headers=business[2]["owner"]).json()
    rival = api.get(url(business[1]), headers=business[2]["other"]).json()
    assert [i["original_filename"] for i in acme] == ["acme.csv"]
    assert [i["original_filename"] for i in rival] == ["rival.csv"]


def test_files_are_kept_in_each_businesss_own_folder(api, business, storage):
    upload(api, business)
    upload(api, business, who="other", org=1)
    folders = {p.parent.name for p in stored_files(storage)}
    assert folders == {business[0], business[1]}


# --- the same file twice ------------------------------------------------------------


def test_uploading_the_same_file_again_warns_but_allows_it(api, business):
    first = upload(api, business, name="september.csv").json()
    second = upload(api, business, name="copy of september.csv").json()
    assert first["duplicate_of"] is None
    assert second["duplicate_of"]["id"] == first["id"]
    assert second["duplicate_of"]["original_filename"] == "september.csv"
    assert second["duplicate_of"]["status"] == "uploaded"
    assert second["id"] != first["id"]


def test_a_different_file_is_not_flagged(api, business):
    upload(api, business)
    assert (
        upload(api, business, CSV + b"30/09/2026,Roll,\xc2\xa32.00\n").json()["duplicate_of"]
        is None
    )


def test_another_business_uploading_the_same_bytes_is_not_flagged(api, business):
    upload(api, business)
    assert upload(api, business, who="other", org=1).json()["duplicate_of"] is None


# --- reading an import back ------------------------------------------------------------


def test_get_an_import_shows_its_details_and_preview(api, business):
    made = upload(api, business).json()
    res = api.get(url(business[0], f"/{made['id']}"), headers=business[2]["owner"])
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == made["id"] and body["row_count"] == 2
    assert body["preview"]["headers"] == ["Date", "Product", "Total"]
    assert body["preview"]["row_count"] == 2  # counted once, at upload


def test_the_list_is_newest_first(api, db, business):
    ids = {}
    for name in ("a.csv", "b.csv", "c.csv"):
        content = CSV + name.encode() + b",1,1\n"
        ids[name] = upload(api, business, name=name, content=content).json()["id"]
    # A test's requests share one transaction (one clock reading); real requests don't.
    with tenant_scope(db, uuid.UUID(business[0])):
        for hour, name in enumerate(("a.csv", "b.csv", "c.csv")):
            imp = db.get(DataImport, uuid.UUID(ids[name]))
            imp.created_at = datetime(2026, 9, 29, 9 + hour, tzinfo=UTC)
        db.flush()
    listed = api.get(url(business[0]), headers=business[2]["owner"]).json()
    assert [i["original_filename"] for i in listed] == ["c.csv", "b.csv", "a.csv"]
    assert "preview" not in listed[0]


def test_an_unknown_import_is_not_found(api, business):
    res = api.get(url(business[0], f"/{uuid.uuid4()}"), headers=business[2]["owner"])
    assert res.status_code == 404


def test_after_the_file_is_purged_the_record_remains_without_a_preview(api, db, business, storage):
    made = upload(api, business).json()
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(made["id"]))
        storage.delete(imp.storage_key)
        imp.file_deleted_at = datetime.now(UTC)
        db.flush()
    res = api.get(url(business[0], f"/{made['id']}"), headers=business[2]["owner"])
    assert res.status_code == 200
    assert res.json()["preview"] is None and res.json()["file_deleted_at"] is not None
    res = patch_import(api, business, made["id"], {"header_row": 2}, "owner")
    assert res.status_code == 409 and res.json()["error"]["code"] == "file_deleted"


# --- choosing the header row or sheet ------------------------------------------------------------


def test_headings_on_a_later_row(api, business, db):
    text = b"Till report\nRun 29/09/2026\nDate,Total\n1/1/2026,5\n2/1/2026,6\n"
    made = upload(api, business, text).json()
    assert made["preview"]["headers"] == ["Till report"]  # row 1 is what it assumed
    res = patch_import(api, business, made["id"], {"header_row": 3}, "owner")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["header_row"], body["row_count"]) == (3, 2)
    assert body["preview"]["headers"] == ["Date", "Total"]
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "import.updated")).one()
    assert entry.details == {"header_row": 3}


def test_changing_the_layout_clears_a_saved_column_mapping(api, business, db):
    made = upload(api, business, b"x\nDate,Total\n1/1,5\n").json()
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(made["id"]))
        imp.column_mapping = {"sold_on": "x"}
        imp.status = "mapped"
        db.flush()
    patch_import(api, business, made["id"], {"header_row": 2}, "owner")
    with tenant_scope(db, uuid.UUID(business[0])):
        db.refresh(imp)
        assert imp.column_mapping == {} and imp.status == "uploaded"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({}, 422, "validation_error"),
        ({"header_row": 0}, 422, "validation_error"),
        ({"header_row": 101}, 422, "validation_error"),
        ({"header_row": 9}, 422, "unreadable_file"),  # the file has no row 9
        ({"sheet_name": "Sales"}, 422, "sheet_only_for_excel"),  # CSV
        ({"colour": "red"}, 422, "validation_error"),
    ],
)
def test_bad_changes_are_refused(api, business, body, status, code):
    made = upload(api, business).json()
    res = api.patch(url(business[0], f"/{made['id']}"), json=body, headers=business[2]["owner"])
    assert res.status_code == status
    assert res.json()["error"]["code"] == code


def test_an_unknown_sheet_is_refused(api, business):
    made = upload(api, business, xlsx({"A": [["x"], [1]], "B": [["y"], [2]]}), name="b.xlsx").json()
    res = patch_import(api, business, made["id"], {"sheet_name": "C"}, "owner")
    assert res.status_code == 422 and res.json()["error"]["code"] == "unknown_sheet"


def test_a_finished_import_cannot_be_changed(api, business, db):
    made = upload(api, business).json()
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(made["id"]))
        imp.status, imp.imported_at = "imported", datetime.now(UTC)
        db.flush()
    res = patch_import(api, business, made["id"], {"header_row": 1}, "owner")
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_editable"


def test_viewers_cannot_change_an_import(api, business):
    made = upload(api, business).json()
    res = patch_import(api, business, made["id"], {"header_row": 1}, "viewer")
    assert res.status_code == 403


# --- file names ------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("sales.csv", "sales.csv"),
        ("C:\\Users\\Jo\\Documents\\sales.csv", "sales.csv"),
        ("/etc/passwd.csv", "passwd.csv"),
        ("../../x.csv", "x.csv"),
        ("bad\x00na\nme.csv", "badname.csv"),
        ("  spaced.csv  ", "spaced.csv"),
        ("", ""),
        (None, ""),
        ("x" * 300 + ".csv", "x" * 251 + ".csv"),
    ],
)
def test_file_names_are_cleaned_for_display(given, shown):
    assert imports_service.clean_filename(given) == shown
