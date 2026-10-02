from pydantic import BaseModel

from app.schemas.imports import ImportOut


class ImportResultOut(BaseModel):
    data_import: ImportOut
    # Rows added to the business's data, by table. Includes customers, products and suppliers
    # created because sales, expenses or stock movements pointed at them.
    created: dict[str, int]
    skipped_duplicates: int  # turned out to be in the data already when the import ran
    skipped_invalid: int  # could no longer be read (should be rare)


class UndoResultOut(BaseModel):
    data_import: ImportOut
    removed: dict[str, int]  # rows taken out of the business's data, by table


class RecordsOut(BaseModel):
    """What this import currently has in the business's data."""

    counts: dict[str, int]
