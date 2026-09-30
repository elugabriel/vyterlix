"""Reading .csv and .xlsx uploads: what's accepted, what's refused, and why."""

import io
import zipfile
from datetime import date, datetime

import openpyxl
import pytest

from app.services import file_reader
from app.services.file_reader import UnreadableFileError, inspect_file

MAX = 1000


def csv_bytes(text: str, encoding: str = "utf-8") -> io.BytesIO:
    return io.BytesIO(text.encode(encoding))


def read_csv(text: str, encoding: str = "utf-8", **kw):
    return inspect_file(csv_bytes(text, encoding), "csv", max_rows=kw.pop("max_rows", MAX), **kw)


def workbook(sheets: dict[str, list[list]], hidden: tuple[str, ...] = ()) -> io.BytesIO:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
        if name in hidden:
            sheet.sheet_state = "hidden"
    out = io.BytesIO()
    book.save(out)
    out.seek(0)
    return out


def read_xlsx(sheets, *, hidden=(), **kw):
    return inspect_file(workbook(sheets, hidden), "excel", max_rows=kw.pop("max_rows", MAX), **kw)


def refused(code, stream, source="csv", **kw):
    with pytest.raises(UnreadableFileError) as caught:
        inspect_file(stream, source, max_rows=kw.pop("max_rows", MAX), **kw)
    assert caught.value.code == code
    assert caught.value.status_code == 422
    return caught.value


# --- CSV ---------------------------------------------------------------------------------------


def test_a_simple_csv():
    p = read_csv("Date,Product,Total\n28/09/2026,Loaf,£4.50\n29/09/2026,Bun,£1.20\n")
    assert p.headers == ["Date", "Product", "Total"]
    assert p.sample_rows == [["28/09/2026", "Loaf", "£4.50"], ["29/09/2026", "Bun", "£1.20"]]
    assert (p.row_count, p.encoding, p.delimiter) == (2, "utf-8-sig", ",")
    assert p.sheets == [] and p.sheet_name is None and not p.needs_sheet


def test_utf8_with_a_byte_order_mark_has_clean_headings():
    p = read_csv("﻿Date,Total\n1/1/2026,5\n")
    assert p.headers[0] == "Date"


def test_excel_uk_csv_in_windows_1252_keeps_the_pound_sign():
    p = read_csv("Item,Price\nCoffee,£3.20\nCafé crème,£4.00\n", encoding="cp1252")
    assert p.encoding == "cp1252"
    assert p.sample_rows == [["Coffee", "£3.20"], ["Café crème", "£4.00"]]


@pytest.mark.parametrize(("separator", "name"), [(";", ";"), ("\t", "\t"), ("|", "|"), (",", ",")])
def test_the_separator_is_detected(separator, name):
    text = (
        separator.join(["Date", "Total", "Note"]) + "\n" + separator.join(["1/1", "5", "x"]) + "\n"
    )
    p = read_csv(text)
    assert p.headers == ["Date", "Total", "Note"]
    assert p.delimiter == name


def test_commas_inside_quotes_and_line_breaks_inside_cells():
    p = read_csv('Name,Note\n"Smith, Jo","line one\nline two"\n')
    assert p.sample_rows == [["Smith, Jo", "line one\nline two"]]
    assert p.row_count == 1


def test_uk_decimal_comma_files_use_semicolons():
    p = read_csv("Total;VAT\n1.234,50;246,90\n")
    assert p.sample_rows == [["1.234,50", "246,90"]]


def test_blank_lines_are_skipped_and_not_counted():
    p = read_csv("A,B\n\n1,2\n , \n3,4\n\n")
    assert p.row_count == 2
    assert p.sample_rows == [["1", "2"], ["3", "4"]]


def test_short_and_long_rows_are_padded_and_trimmed_to_the_headings():
    p = read_csv("A,B,C\n1\n1,2,3,4,5\n")
    assert p.sample_rows == [["1", "", ""], ["1", "2", "3"]]


def test_blank_and_repeated_headings_are_made_usable():
    p = read_csv("Total,,total,Total,Date\n1,2,3,4,5\n")
    assert p.headers == ["Total", "Column 2", "total (2)", "Total (3)", "Date"]


def test_trailing_empty_columns_are_not_columns():
    assert read_csv("A,B,,,\n1,2,,,\n").headers == ["A", "B"]


def test_headings_on_a_later_row():
    p = read_csv("Till report\nRun on 29/09/2026\nDate,Total\n1/1/2026,5\n", header_row=3)
    assert p.headers == ["Date", "Total"]
    assert p.row_count == 1


def test_only_20_sample_rows_but_every_row_counted():
    text = "N\n" + "\n".join(str(i) for i in range(1, 251)) + "\n"
    p = read_csv(text)
    assert (len(p.sample_rows), p.row_count) == (20, 250)
    assert p.sample_rows[-1] == ["20"]


def test_a_preview_can_skip_counting():
    p = read_csv("N\n" + "\n".join(str(i) for i in range(500)) + "\n", count=False)
    assert len(p.sample_rows) == 20 and p.row_count == 0


def test_too_many_rows():
    text = "N\n" + "\n".join(str(i) for i in range(1, 12)) + "\n"
    error = refused("too_many_rows", csv_bytes(text), max_rows=10)
    assert "10" in error.message


def test_exactly_the_row_limit_is_fine():
    text = "N\n" + "\n".join(str(i) for i in range(1, 11)) + "\n"
    assert read_csv(text, max_rows=10).row_count == 10


def test_too_many_columns():
    refused("too_many_columns", csv_bytes(",".join(f"c{i}" for i in range(201)) + "\n1\n"))


def test_headings_only_is_refused():
    refused("no_data_rows", csv_bytes("Date,Total\n"))
    refused("no_data_rows", csv_bytes("Date,Total\n\n\n"))


def test_an_empty_file_is_refused():
    refused("empty_file", csv_bytes(""))


def test_a_blank_heading_row_is_refused():
    refused("no_headers", csv_bytes(",,,\n1,2,3\n"))


def test_a_header_row_beyond_the_end_of_the_file():
    refused("unreadable_file", csv_bytes("A,B\n1,2\n"), header_row=9)


@pytest.mark.parametrize("header_row", [0, -1, 101])
def test_header_row_range(header_row):
    refused("bad_header_row", csv_bytes("A\n1\n"), header_row=header_row)


def test_a_cell_that_is_absurdly_large_is_refused_cleanly():
    refused("unreadable_file", csv_bytes("A\n" + "x" * 200_000 + "\n"))


def test_undecodable_text_is_refused_not_crashed():
    # Bytes that are neither valid UTF-8 nor defined in Windows-1252.
    refused("bad_encoding", io.BytesIO(b"A,B\n1,\x81\x8d\n"))


def test_utf16_csv_is_refused_with_advice():
    error = refused("unsupported_file_type", csv_bytes("A,B\n1,2\n", "utf-16"))
    assert "UTF-8" in error.message


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("pdf", b"%PDF-1.7\n1 0 obj"),
        ("png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20),
        ("jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 20),
        ("exe", b"MZ\x90\x00\x03" + b"\x00" * 20),
        ("gzip", b"\x1f\x8b\x08\x00" + b"\x00" * 20),
        ("binary", b"A,B\n1,\x00\x01\x02\n"),
    ],
)
def test_csv_that_is_really_something_else(label, content):
    refused("unsupported_file_type", io.BytesIO(content))


def test_a_renamed_xlsx_is_not_a_csv():
    error = refused("unsupported_file_type", workbook({"S": [["A"], [1]]}))
    assert ".xlsx" in error.message


def test_old_xls_is_refused_with_advice_for_both_kinds():
    xls = io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    assert ".xlsx" in refused("unsupported_file_type", xls, source="csv").message
    xls.seek(0)
    assert ".xlsx" in refused("unsupported_file_type", xls, source="excel").message


def test_the_stream_is_left_open_and_reusable():
    stream = csv_bytes("A\n1\n")
    inspect_file(stream, "csv", max_rows=MAX)
    assert not stream.closed
    assert stream.read(1) in (b"A", b"")  # readable; position is the caller's business


# --- Excel -------------------------------------------------------------------------------------


def test_a_simple_workbook():
    p = read_xlsx({"Sales": [["Date", "Product", "Total"], [date(2026, 9, 28), "Loaf", 4.5]]})
    assert p.headers == ["Date", "Product", "Total"]
    assert p.sample_rows == [["2026-09-28", "Loaf", "4.5"]]
    assert (p.row_count, p.sheets, p.sheet_name) == (1, ["Sales"], "Sales")
    assert p.encoding is None and p.delimiter is None


def test_cell_values_become_text_predictably():
    p = read_xlsx(
        {
            "S": [
                ["v"] * 8,
                [
                    12,
                    0.1,
                    0.00001,
                    True,
                    datetime(2026, 9, 28),
                    datetime(2026, 9, 28, 14, 30),
                    None,
                    "  padded  ",
                ],
            ]
        }
    )
    assert p.sample_rows[0] == [
        "12",
        "0.1",
        "0.00001",
        "TRUE",
        "2026-09-28",
        "2026-09-28 14:30:00",
        "",
        "  padded  ",
    ]


def test_several_sheets_need_a_choice():
    sheets = {"Summary": [["Note"], ["hi"]], "Sales": [["Date"], ["1/1"]]}
    p = read_xlsx(sheets)
    assert p.needs_sheet and p.sheets == ["Summary", "Sales"]
    assert p.headers == [] and p.row_count == 0


def test_a_chosen_sheet_is_read():
    sheets = {"Summary": [["Note"], ["hi"]], "Sales": [["Date", "Total"], ["1/1", 5], ["2/1", 6]]}
    p = read_xlsx(sheets, sheet_name="Sales")
    assert (p.headers, p.row_count, p.sheet_name) == (["Date", "Total"], 2, "Sales")
    assert not p.needs_sheet


def test_hidden_sheets_are_not_offered():
    sheets = {"Data": [["A"], [1]], "Scratch": [["Z"], [9]]}
    p = read_xlsx(sheets, hidden=("Scratch",))
    assert p.sheets == ["Data"] and p.sheet_name == "Data" and not p.needs_sheet
    refused("unknown_sheet", workbook(sheets, ("Scratch",)), source="excel", sheet_name="Scratch")


def test_an_unknown_sheet_is_refused():
    error = refused("unknown_sheet", workbook({"A": [["x"], [1]]}), "excel", sheet_name="Nope")
    assert "Nope" in error.message


def test_excel_headings_on_a_later_row_and_blank_rows_skipped():
    rows = [["Report"], [None], ["Date", "Total"], ["1/1", 5], [None, None], ["2/1", 6]]
    p = read_xlsx({"S": rows}, header_row=3)
    assert p.headers == ["Date", "Total"] and p.row_count == 2


def test_excel_row_limit():
    rows = [["N"], *[[i] for i in range(11)]]
    refused("too_many_rows", workbook({"S": rows}), "excel", max_rows=10)


def test_an_empty_workbook_sheet_is_refused():
    refused("unreadable_file", workbook({"S": []}), "excel", sheet_name="S")
    refused("no_data_rows", workbook({"S": [["A", "B"]]}), "excel")


def test_formulas_show_their_saved_values_or_blank():
    # openpyxl saves formulas without results, like a workbook never opened in Excel.
    p = read_xlsx({"S": [["A", "B"], [2, "=A2*2"]]})
    assert p.sample_rows == [["2", ""]]


def test_a_workbook_with_macros_is_refused():
    raw = workbook({"S": [["A"], [1]]}).getvalue()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("xl/vbaProject.bin", b"macro")
    out.seek(0)
    assert "macros" in refused("unsupported_file_type", out, "excel").message


def test_a_zip_that_is_not_a_workbook_is_refused():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("readme.txt", "hello")
    out.seek(0)
    refused("unsupported_file_type", out, "excel")


def test_a_damaged_workbook_is_refused_not_crashed():
    raw = workbook({"S": [["A"], [1]]}).getvalue()
    refused("unreadable_file", io.BytesIO(raw[: len(raw) // 2]), "excel")
    refused("unsupported_file_type", io.BytesIO(b"not a zip at all"), "excel")


def test_a_zip_bomb_is_refused_before_unpacking(monkeypatch):
    monkeypatch.setattr(file_reader, "MAX_UNPACKED_BYTES", 500)
    refused("file_too_large", workbook({"S": [["A"] * 50, ["x" * 100] * 50]}), "excel")


def test_too_many_files_inside_the_zip(monkeypatch):
    monkeypatch.setattr(file_reader, "MAX_ZIP_ENTRIES", 3)
    refused("file_too_large", workbook({"S": [["A"], [1]]}), "excel")


def test_password_protected_workbook_gets_advice():
    # Encrypted workbooks are OLE containers, not zips.
    error = refused(
        "unsupported_file_type", io.BytesIO(b"\xd0\xcf\x11\xe0" + b"\x00" * 50), "excel"
    )
    assert "password" in error.message


def test_a_csv_renamed_to_xlsx_is_refused():
    error = refused("unsupported_file_type", csv_bytes("A,B\n1,2\n"), "excel")
    assert ".xlsx" in error.message


def test_a_workbook_that_understates_its_own_size_is_still_read_in_full():
    """Some tools (exports, Google Sheets, scripts) write a wrong <dimension>; trusting it
    would silently drop rows."""
    import re

    raw = workbook({"S": [["A", "B"], *[[i, i * 2] for i in range(1, 11)]]}).getvalue()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data, changed = re.subn(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:B3"', data)
                assert changed == 1
            dst.writestr(item, data)
    out.seek(0)
    p = inspect_file(out, "excel", max_rows=MAX)
    assert p.row_count == 10
