import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.models.imports import DATASETS
from app.schemas.import_mapping import DataSourceOut, MappingIn, MappingOut
from app.schemas.imports import ImportDetailOut, ImportOut, ImportPatch, ImportUploadedOut
from app.services.import_mapping import get_mapping, list_sources, save_mapping
from app.services.imports import get_import, list_imports, update_import, upload_import
from app.services.storage import FileStorage, get_file_storage

router = APIRouter(prefix="/organizations/{organization_id}/imports", tags=["imports"])

sources_router = APIRouter(prefix="/organizations/{organization_id}/data-sources", tags=["imports"])

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


@router.get("/{import_id}/mapping", response_model=MappingOut)
def mapping(import_id: uuid.UUID, tenant: DataManager, db: DB, storage: Storage):
    """The file's columns, the fields they can fill, our suggestions, and what's still missing."""
    return get_mapping(db, storage, import_id)


@router.put("/{import_id}/mapping", response_model=MappingOut)
def save_mapping_(
    import_id: uuid.UUID,
    body: MappingIn,
    tenant: DataManager,
    db: DB,
    storage: Storage,
    meta: Meta,
):
    """Save which column feeds which field. Incomplete mappings are refused (422 with the list
    of problems), so a saved mapping is always ready for validation."""
    return save_mapping(db, storage, tenant, import_id, body, meta)


@sources_router.get("", response_model=list[DataSourceOut])
def list_data_sources(tenant: DataManager, db: DB):
    """Mappings saved under a name, e.g. "Till export"."""
    return list_sources(db)
