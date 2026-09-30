import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.models.imports import DATASETS
from app.schemas.imports import ImportDetailOut, ImportOut, ImportPatch, ImportUploadedOut
from app.services.imports import get_import, list_imports, update_import, upload_import
from app.services.storage import FileStorage, get_file_storage

router = APIRouter(prefix="/organizations/{organization_id}/imports", tags=["imports"])

DataManager = Annotated[Tenant, Depends(require_permission(Perm.DATA_MANAGE))]
Storage = Annotated[FileStorage, Depends(get_file_storage)]
Dataset = Annotated[str, Form(pattern=f"^({'|'.join(DATASETS)})$")]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ImportUploadedOut)
def upload(
    tenant: DataManager,
    db: DB,
    storage: Storage,
    meta: Meta,
    file: Annotated[UploadFile, File(description="A .csv or .xlsx file, up to 25 MB")],
    dataset: Dataset,
):
    """Upload a file. Returns what's in it (headings, sample rows, row count) so the user can
    check it; nothing is imported yet."""
    return upload_import(
        db, storage, tenant, stream=file.file, filename=file.filename, dataset=dataset, meta=meta
    )


@router.get("", response_model=list[ImportOut])
def list_(tenant: DataManager, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return list_imports(db, limit)


@router.get("/{import_id}", response_model=ImportDetailOut)
def get(import_id: uuid.UUID, tenant: DataManager, db: DB, storage: Storage):
    return get_import(db, storage, import_id)


@router.patch("/{import_id}", response_model=ImportDetailOut)
def update(
    import_id: uuid.UUID,
    body: ImportPatch,
    tenant: DataManager,
    db: DB,
    storage: Storage,
    meta: Meta,
):
    """Pick the Excel sheet (when the workbook has several) or the row holding the headings."""
    return update_import(db, storage, tenant, import_id, body, meta)
