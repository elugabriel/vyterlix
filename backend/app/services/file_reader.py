"""Reading uploaded .csv and .xlsx files safely.

Nothing here trusts the file: the name and extension are only a hint, the content is
checked, and every limit (rows, columns, cell size, unpacked size) is enforced while
reading, so a hostile or accidental giant file can't exhaust memory.

What we accept, and why:
- .csv in UTF-8 (with or without a BOM) or Windows-1252, which is what Excel in the UK
  writes ("£" stays "£"); comma, semicolon, tab or pipe separated.
- .xlsx only (decision 2026-09-28). Older .xls, macro-enabled and password-protected
  workbooks are refused with a message saying what to do instead.

Cell values are returned as text exactly as the file holds them, except Excel dates,
which are written as ISO dates (2026-09-28) because that is unambiguous; interpreting
the text (UK dd/mm/yyyy, "£1,234.50") is the job of validation (step 5).
"""

import codecs
import csv
import io
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import BinaryIO

from app.core.errors import AppError

SAMPLE_ROWS = 20
MAX_COLUMNS = 200
MAX_HEADER_LENGTH = 100
MAX_HEADER_ROW = 100
MAX_CELL_CHARS = 10_000
MAX_ZIP_ENTRIES = 2_000
MAX_UNPACKED_BYTES = 300 * 1024 * 1024  # what a 25 MB workbook may expand to (zip bombs)
DELIMITERS = (",", ";", "\t", "|")

_OLD_EXCEL = b"\xd0\xcf\x11\xe0"  # .xls, and password-protected .xlsx
_ZIP = b"PK\x03\x04"


class UnreadableFileError(AppError):
    status_code = 422
    code = "unreadable_file"


@dataclass
class FilePreview:
    headers: list[str] = field(default_factory=list)
    sample_rows: list[list[str]] = field(default_factory=list)
    row_count: int = 0  # data rows, excluding blank ones (0 if not counted)
    sheets: list[str] = field(default_factory=list)  # Excel only
    sheet_name: str | None = None  # Excel only: the one that was read
    needs_sheet: bool = False  # several sheets and none chosen yet
    encoding: str | None = None  # CSV only
    delimiter: str | None = None  # CSV only


def _fail(message: str, code: str = "unreadable_file") -> UnreadableFileError:
    return UnreadableFileError(message, code=code)


# --- what kind of file is this? -----------------------------------------------------------------


def check_content(stream: BinaryIO, source: str) -> None:
    """Refuse files whose content isn't what their extension claims."""
    stream.seek(0)
    head = stream.read(8192)
    stream.seek(0)
    if not head:
        raise _fail("That file is empty.", "empty_file")
    if head.startswith(_OLD_EXCEL):
        raise _fail(
            "That looks like an older Excel (.xls) or password-protected file. "
            "In Excel choose File > Save As > Excel Workbook (.xlsx), without a password, "
            "and upload that.",
            "unsupported_file_type",
        )
    if source == "excel":
        if not head.startswith(_ZIP):
            raise _fail(
                "That file isn't a real Excel (.xlsx) workbook. Open it in Excel and "
                "save it as an Excel Workbook (.xlsx).",
                "unsupported_file_type",
            )
        return
    # CSV: plain text only. Zips (a renamed .xlsx), PDFs, images and programs are refused.
    if head.startswith((_ZIP, b"%PDF", b"MZ", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"\x1f\x8b")):
        raise _fail(
            "That file isn't a CSV. If it's an Excel workbook, save it with the .xlsx "
            "extension and upload it again.",
            "unsupported_file_type",
        )
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise _fail("That file isn't a text (CSV) file.", "unsupported_file_type")
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise _fail(
            "That CSV is saved as UTF-16. In Excel choose Save As > CSV UTF-8 "
            "(Comma delimited) and upload that.",
            "unsupported_file_type",
        )


# --- shared helpers -----------------------------------------------------------------------------


def _text(value: object) -> str:
    """One cell as text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, float):
        return format(Decimal(repr(value)), "f") if value == value else ""  # no 1e-05, no NaN
    text = str(value)
    return text[:MAX_CELL_CHARS]


def _clean_headers(cells: Iterable[object]) -> list[str]:
    """Trim, name blank columns, and make duplicates unique ("Total", "Total (2)")."""
    raw = [_text(c).strip() for c in cells]
    while raw and not raw[-1]:
        raw.pop()  # trailing empty columns are not columns
    if not raw:
        raise _fail(
            "The header row is empty. Check which row holds your column names.", "no_headers"
        )
    if len(raw) > MAX_COLUMNS:
        raise _fail(f"That file has more than {MAX_COLUMNS} columns.", "too_many_columns")
    seen: dict[str, int] = {}
    headers = []
    for position, name in enumerate(raw, start=1):
        name = (name or f"Column {position}")[:MAX_HEADER_LENGTH]
        count = seen.get(name.casefold(), 0) + 1
        seen[name.casefold()] = count
        headers.append(name if count == 1 else f"{name} ({count})")
    return headers


def _collect(
    header_cells: Iterable[object],
    data_rows: Iterator[Iterable[object]],
    *,
    max_rows: int,
    count: bool,
) -> FilePreview:
    """Headers, a sample, and (when `count`) the number of data rows, skipping blank rows."""
    preview = FilePreview(headers=_clean_headers(header_cells))
    width = len(preview.headers)
    for row in data_rows:
        cells = [_text(c) for c in list(row)[:width]]
        if not any(c.strip() for c in cells):
            continue  # blank line
        preview.row_count += 1
        if preview.row_count > max_rows:
            raise _fail(
                f"That file has more than {max_rows:,} rows. Split it into smaller files "
                "(for example one per year) and upload them one at a time.",
                "too_many_rows",
            )
        if len(preview.sample_rows) < SAMPLE_ROWS:
            preview.sample_rows.append(cells + [""] * (width - len(cells)))
        elif not count:
            break
    if preview.row_count == 0:
        raise _fail("There are column headings but no data rows under them.", "no_data_rows")
    if not count:
        preview.row_count = 0  # stopped after the sample, so there is no real count
    return preview


def _skip(rows: Iterator[Iterable[object]], header_row: int) -> Iterable[object] | None:
    """Advance to the header row (1-based). None if the file ends first."""
    for _ in range(header_row - 1):
        if next(rows, None) is None:
            return None
    return next(rows, None)


# --- CSV ----------------------------------------------------------------------------------------


def _detect_encoding(stream: BinaryIO) -> str:
    """UTF-8 if the whole file decodes as UTF-8, otherwise Windows-1252 (UK Excel default)."""
    stream.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        while chunk := stream.read(1024 * 1024):
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"
    finally:
        stream.seek(0)


def _detect_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in DELIMITERS}
    best = max(DELIMITERS, key=lambda d: (counts[d], d == ","))
    return best if counts[best] else ","


def _read_csv(stream: BinaryIO, *, header_row: int, max_rows: int, count: bool) -> FilePreview:
    encoding = _detect_encoding(stream)
    text = io.TextIOWrapper(stream, encoding=encoding, newline="")
    no_header = _fail(f"The file has no row {header_row} to use as the column headings.")
    try:
        lines = text.readlines(64 * 1024)  # enough to find the header line
        if len(lines) < header_row:
            raise no_header
        delimiter = _detect_delimiter(lines[header_row - 1])
        text.seek(0)
        rows = csv.reader(text, delimiter=delimiter, strict=False)
        header_cells = _skip(rows, header_row)
        if header_cells is None:
            raise no_header
        preview = _collect(header_cells, rows, max_rows=max_rows, count=count)
    except UnicodeDecodeError:
        raise _fail(
            "Some characters in that file can't be read. Save it as CSV UTF-8 and upload it again.",
            "bad_encoding",
        ) from None
    except csv.Error as exc:
        raise _fail(f"That file isn't valid CSV ({exc}).") from None
    finally:
        text.detach()  # leave the caller's stream open
    preview.encoding, preview.delimiter = encoding, delimiter
    return preview


# --- Excel --------------------------------------------------------------------------------------


def _check_workbook_zip(stream: BinaryIO) -> None:
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            names = {e.filename for e in entries}
    except zipfile.BadZipFile:
        raise _fail("That Excel file is damaged or isn't a real .xlsx workbook.") from None
    if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
        raise _fail("That Excel file is too large once unpacked.", "file_too_large")
    if "xl/workbook.xml" not in names:
        raise _fail(
            "That file isn't an Excel workbook (a .xlsx file with no workbook inside).",
            "unsupported_file_type",
        )
    if "xl/vbaProject.bin" in names:
        raise _fail(
            "Workbooks with macros aren't accepted. Save a copy as a plain Excel Workbook "
            "(.xlsx) without macros.",
            "unsupported_file_type",
        )
    stream.seek(0)


def _read_excel(
    stream: BinaryIO, *, sheet_name: str | None, header_row: int, max_rows: int, count: bool
) -> FilePreview:
    import openpyxl  # imported here: it's slow to import and only Excel uploads need it

    _check_workbook_zip(stream)
    try:
        book = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    except Exception:  # openpyxl raises many types for corrupt files
        raise _fail("That Excel file couldn't be opened. It may be damaged.") from None
    try:
        sheets = [s.title for s in book.worksheets if s.sheet_state == "visible"]
        if not sheets:
            raise _fail("That workbook has no visible sheets.", "empty_file")
        if sheet_name is None:
            if len(sheets) > 1:
                return FilePreview(sheets=sheets, needs_sheet=True)
            sheet_name = sheets[0]
        elif sheet_name not in sheets:
            raise _fail(f"That workbook has no sheet called '{sheet_name}'.", "unknown_sheet")
        sheet = book[sheet_name]
        sheet.reset_dimensions()  # some tools write wrong sizes, which would cut rows off
        rows = sheet.iter_rows(values_only=True)
        header_cells = _skip(rows, header_row)
        if header_cells is None:
            raise _fail(f"Sheet '{sheet_name}' has no row {header_row} to use as headings.")
        preview = _collect(header_cells, rows, max_rows=max_rows, count=count)
    finally:
        book.close()
    preview.sheets, preview.sheet_name = sheets, sheet_name
    return preview


# --- entry point --------------------------------------------------------------------------------


def inspect_file(
    stream: BinaryIO,
    source: str,
    *,
    sheet_name: str | None = None,
    header_row: int = 1,
    max_rows: int,
    count: bool = True,
) -> FilePreview:
    """Check a stored upload and read its headings, a sample and (optionally) its row count.

    `count=False` stops after the sample: faster, for re-showing a preview.
    """
    if not 1 <= header_row <= MAX_HEADER_ROW:
        raise _fail(f"The header row must be between 1 and {MAX_HEADER_ROW}.", "bad_header_row")
    check_content(stream, source)
    if source == "csv":
        return _read_csv(stream, header_row=header_row, max_rows=max_rows, count=count)
    return _read_excel(
        stream, sheet_name=sheet_name, header_row=header_row, max_rows=max_rows, count=count
    )
