"""Matching an upload's columns to Vyterlix fields (Phase 4 step 4).

The person sees the file's columns, our suggestions (from a mapping they saved earlier
for the same kind of file, else from well-known column names) and the questions only
they can answer (do the amounts include VAT?). When they save, the mapping is checked
completely; nothing incomplete is stored, so validation (step 5) can trust it.
The session must be scoped to the organisation (CurrentTenant).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError
from app.models.imports import DataImport, DataSource
from app.schemas.import_mapping import (
    DataSourceOut,
    FieldOut,
    IssueOut,
    MappingIn,
    MappingOptions,
    MappingOut,
    SavedSourceOut,
)
from app.services import import_fields as fields
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.imports import EDITABLE, get_record, read_preview
from app.services.storage import FileStorage


def _file_headers(storage: FileStorage, data_import: DataImport) -> list[str]:
    preview = read_preview(storage, data_import, count=False)
    if preview is None:
        raise ConflictError("The original file has been deleted", code="file_deleted")
    if preview.needs_sheet:
        raise AppError(
            "This workbook has several sheets. Choose the sheet first.",
            code="choose_sheet_first",
            status_code=409,
        )
    return preview.headers


def _field_outs(dataset: str) -> list[FieldOut]:
    in_group = {k: list(g) for g in fields.ONE_OF.get(dataset, ()) for k in g}
    return [
        FieldOut(
            key=f.key,
            label=f.label,
            help=f.help,
            type=f.type,
            required=f.key in fields.REQUIRED[dataset],
            one_of=in_group.get(f.key),
        )
        for f in fields.DATASET_FIELDS[dataset]
    ]


def _saved_suggestion(
    db: Session, dataset: str, headers: list[str]
) -> tuple[DataSource, dict[str, str]] | None:
    """The most recently used saved mapping for this dataset whose columns all exist in this
    file (matched ignoring case and punctuation) and which is complete for it."""
    by_name = {}
    for header in headers:
        by_name.setdefault(fields.normalise(header), header)
    sources = db.scalars(
        select(DataSource)
        .where(DataSource.dataset == dataset)
        .order_by(DataSource.last_imported_at.desc().nulls_last(), DataSource.updated_at.desc())
    )
    for source in sources:
        resolved = {k: by_name.get(fields.normalise(h)) for k, h in source.column_mapping.items()}
        if resolved and all(resolved.values()):
            if not fields.check_mapping(dataset, resolved, {}, headers, require_answers=False):
                return source, resolved
    return None


def _out(db: Session, data_import: DataImport, headers: list[str]) -> MappingOut:
    dataset = data_import.dataset
    saved = _saved_suggestion(db, dataset, headers)
    if saved:
        source, suggested = saved
        suggested_options = MappingOptions.model_validate(source.options or {})
        origin, saved_source = "saved", SavedSourceOut(id=source.id, name=source.name)
    else:
        suggested = fields.suggest_mapping(dataset, headers)
        suggested_options = MappingOptions()
        origin, saved_source = ("automatic" if suggested else None), None

    amount_field = fields.MONEY_AMOUNT_FIELD.get(dataset)
    if suggested_options.vat_inclusive is None and amount_field:
        hint = fields.suggest_vat_inclusive(
            (data_import.column_mapping.get(amount_field)) or suggested.get(amount_field)
        )
        # Most sales files are consumer takings, which include VAT (decision 2026-09-28).
        suggested_options.vat_inclusive = hint if hint is not None else dataset == "sales" or None

    issues = fields.check_mapping(dataset, data_import.column_mapping, data_import.options, headers)
    return MappingOut(
        dataset=dataset,
        status=data_import.status,
        fields=_field_outs(dataset),
        headers=headers,
        mapping=data_import.column_mapping,
        options=data_import.options,
        suggested_mapping=suggested,
        suggested_options=suggested_options,
        suggestion_from=origin,
        saved_source=saved_source,
        needs_vat_options=fields.needs_vat_options(dataset),
        vat_rates=list(fields.VAT_RATES),
        issues=[IssueOut(code=i.code, message=i.message, field=i.field) for i in issues],
        ready=bool(data_import.column_mapping) and not issues,
    )


def get_mapping(db: Session, storage: FileStorage, import_id) -> MappingOut:
    data_import = get_record(db, import_id)
    return _out(db, data_import, _file_headers(storage, data_import))


def save_mapping(
    db: Session,
    storage: FileStorage,
    tenant,
    import_id,
    body: MappingIn,
    meta: RequestMeta,
) -> MappingOut:
    data_import = get_record(db, import_id)
    if data_import.status not in EDITABLE:
        raise ConflictError(
            "This import has already run, so its mapping can't be changed", code="not_editable"
        )
    headers = _file_headers(storage, data_import)

    mapping = {k: h for k, h in body.mapping.items() if h is not None and h.strip() != ""}
    options = body.options.model_dump(exclude_none=True)
    issues = fields.check_mapping(data_import.dataset, mapping, options, headers)
    if issues:
        raise AppError(
            "The column mapping has problems",
            code="mapping_invalid",
            status_code=422,
            details=[{"code": i.code, "message": i.message, "field": i.field} for i in issues],
        )

    source = _remember(db, data_import, body.save_as, mapping, options)
    data_import.column_mapping = mapping
    data_import.options = options
    data_import.status = "mapped"
    # Anything validated under an earlier mapping is out of date.
    data_import.valid_count = data_import.invalid_count = data_import.duplicate_count = 0
    if source is not None:
        data_import.data_source_id = source.id
    record_audit(
        db,
        AuditAction.IMPORT_MAPPED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="data_import",
        target_id=data_import.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={
            "dataset": data_import.dataset,
            "fields": sorted(mapping),
            "options": options,
            "saved_as": source.name if source else None,
        },
    )
    db.commit()
    db.refresh(data_import)
    return _out(db, data_import, headers)


def _remember(db, data_import, name, mapping, options) -> DataSource | None:
    if name is None:
        return None
    source = db.scalar(select(DataSource).where(DataSource.name == name))
    if source is not None and source.dataset != data_import.dataset:
        raise ConflictError(
            f"'{name}' is already used for {source.dataset} files. Choose another name.",
            code="source_dataset_mismatch",
        )
    if source is None:
        source = DataSource(name=name, dataset=data_import.dataset)
        db.add(source)
    source.kind = data_import.source
    source.column_mapping = mapping
    source.options = options
    db.flush()
    return source


def list_sources(db: Session) -> list[DataSourceOut]:
    rows = db.scalars(select(DataSource).order_by(DataSource.name))
    return [DataSourceOut.model_validate(r) for r in rows]
