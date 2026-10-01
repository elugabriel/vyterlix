"""Reading cell text the UK way: strict, and never guessing."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.value_parsers import (
    CellError,
    clean_text,
    parse_date,
    parse_email,
    parse_money,
    parse_postcode,
    parse_quantity,
    parse_vat_rate,
    round_pennies,
)

D = Decimal
TODAY = date(2026, 9, 30)


def code_of(parser, text, **kw):
    with pytest.raises(CellError) as caught:
        parser(text, **kw)
    return caught.value.code


# --- dates ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("28/09/2026", date(2026, 9, 28)),
        ("1/2/2026", date(2026, 2, 1)),  # day first: 1 February, not 2 January
        ("01/02/2026", date(2026, 2, 1)),
        ("28-09-2026", date(2026, 9, 28)),
        ("28.09.2026", date(2026, 9, 28)),
        ("28 09 2026", date(2026, 9, 28)),
        ("28/09/26", date(2026, 9, 28)),
        ("28/09/99", date(1999, 9, 28)),
        ("28/09/2026 14:30", date(2026, 9, 28)),
        ("28/09/2026 14:30:15", date(2026, 9, 28)),
        ("2026-09-28", date(2026, 9, 28)),
        ("2026-9-8", date(2026, 9, 8)),
        ("2026-09-28 14:30:00", date(2026, 9, 28)),
        ("2026-09-28T14:30:00Z", date(2026, 9, 28)),
        ("2026-09-28T14:30:00+01:00", date(2026, 9, 28)),
        ("28 Sep 2026", date(2026, 9, 28)),
        ("28 September 2026", date(2026, 9, 28)),
        ("28th September 2026", date(2026, 9, 28)),
        ("1st Oct 26", date(2026, 10, 1)),
        ("28-Sep-2026", date(2026, 9, 28)),
        ("28/Sept/2026", date(2026, 9, 28)),
        ("  28/09/2026  ", date(2026, 9, 28)),
        ("29/02/2024", date(2024, 2, 29)),  # a real leap day
        ("01/10/2026", date(2026, 10, 1)),  # tomorrow is allowed (UK time zone slack)
    ],
)
def test_dates_that_are_read(text, expected):
    assert parse_date(text, today=TODAY) == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("09/28/2026", "invalid_date"),  # US style: there is no month 28
        ("31/04/2026", "invalid_date"),
        ("29/02/2026", "invalid_date"),  # not a leap year
        ("00/09/2026", "invalid_date"),
        ("32/01/2026", "invalid_date"),
        ("28 Foo 2026", "invalid_date"),
        ("September 2026", "invalid_date"),
        ("2026/09/28", "invalid_date"),
        ("28/9", "invalid_date"),
        ("46293", "invalid_date"),  # an Excel serial number
        ("yesterday", "invalid_date"),
        ("", "invalid_date"),
        ("28/09/2099", "date_out_of_range"),
        ("02/10/2026", "date_out_of_range"),  # the day after tomorrow
        ("31/12/1989", "date_out_of_range"),
    ],
)
def test_dates_that_are_refused(text, code):
    assert code_of(parse_date, text, today=TODAY) == code


def test_us_dates_are_explained_not_silently_swapped():
    with pytest.raises(CellError) as caught:
        parse_date("09/28/2026", today=TODAY)
    assert "day/month/year" in caught.value.message and "28" in caught.value.message


def test_an_ambiguous_date_is_read_day_first():
    assert parse_date("03/04/2026", today=TODAY) == date(2026, 4, 3)


def test_the_date_limit_uses_uk_today_by_default():
    assert parse_date("01/01/2020") == date(2020, 1, 1)


# --- money ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", D("0")),
        ("12", D("12")),
        ("12.5", D("12.5")),
        ("1234.50", D("1234.50")),
        ("£1,234.50", D("1234.50")),
        ("£ 5", D("5")),
        ("£5.00", D("5.00")),
        ("5.00 GBP", D("5.00")),
        ("GBP 7.5", D("7.5")),
        ("gbp7.5", D("7.5")),
        ("1,234,567.89", D("1234567.89")),
        (".5", D("0.5")),
        ("-12.50", D("-12.50")),
        ("-£5.00", D("-5.00")),
        ("£-5.00", D("-5.00")),
        ("(12.50)", D("-12.50")),
        ("(£12.50)", D("-12.50")),
        ("12.50-", D("-12.50")),
        ("−12.50", D("-12.50")),  # a typographic minus
        ("+12.50", D("12.50")),
        ("0.1234", D("0.1234")),  # unit costs can be fractions of a penny
        (" 12.50 ", D("12.50")),
    ],
)
def test_amounts_that_are_read(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("4,50", "invalid_amount"),  # comma for pence: never read as 450
        ("4,5", "invalid_amount"),
        ("1,23", "invalid_amount"),
        ("1,2345", "invalid_amount"),
        ("1 234.50", "invalid_amount"),
        ("1.234,50", "invalid_amount"),
        ("12.50.1", "invalid_amount"),
        ("1e5", "invalid_amount"),
        ("abc", "invalid_amount"),
        ("£", "invalid_amount"),
        (".", "invalid_amount"),
        ("--5", "invalid_amount"),
        ("NaN", "invalid_amount"),
        ("Infinity", "invalid_amount"),
        ("", "invalid_amount"),
        ("$5", "wrong_currency"),
        ("€5.00", "wrong_currency"),
        ("5 USD", "wrong_currency"),
        ("5 euro", "wrong_currency"),
        ("0.12345", "too_many_decimals"),
        ("1000000000000", "amount_too_large"),
    ],
)
def test_amounts_that_are_refused(text, code):
    assert code_of(parse_money, text) == code


def test_the_comma_for_pence_message_tells_them_what_to_do():
    with pytest.raises(CellError) as caught:
        parse_money("4,50")
    assert "full stop" in caught.value.message


def test_rounding_is_to_the_nearest_penny_with_halves_going_up():
    assert round_pennies(D("1.005")) == D("1.01")
    assert round_pennies(D("2.675")) == D("2.68")
    assert round_pennies(D("-1.005")) == D("-1.01")
    assert round_pennies(D("1.004")) == D("1.00")


# --- quantities ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3", D("3")),
        ("2.5", D("2.5")),
        ("-4", D("-4")),
        ("1,200", D("1200")),
        ("0.0001", D("0.0001")),
    ],
)
def test_quantities_that_are_read(text, expected):
    assert parse_quantity(text) == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("£3", "invalid_amount"),
        ("3 boxes", "invalid_amount"),
        ("1,5", "invalid_amount"),
        ("0.00001", "too_many_decimals"),
        ("1000000000", "amount_too_large"),
    ],
)
def test_quantities_that_are_refused(text, code):
    assert code_of(parse_quantity, text) == code


# --- VAT rates -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20", D("20")),
        ("20%", D("20")),
        (" 20 % ", D("20")),
        ("5", D("5")),
        ("5.0", D("5")),
        ("0", D("0")),
        ("0%", D("0")),
        ("20.00", D("20")),
    ],
)
def test_uk_vat_rates(text, expected):
    assert parse_vat_rate(text) == expected


@pytest.mark.parametrize("text", ["17.5", "15", "0.2", "21", "-20", "twenty", "", "20 %%", "VAT"])
def test_other_vat_rates_are_refused(text):
    assert code_of(parse_vat_rate, text) == "invalid_vat_rate"


# --- text, email, postcode -----------------------------------------------------------------------


def test_text_is_tidied():
    assert clean_text("  Sour\tdough \n loaf  ", max_length=50, what="name") == "Sour dough loaf"


def test_text_that_is_too_long_is_refused():
    assert code_of(lambda t: clean_text(t, max_length=5, what="name"), "abcdef") == "too_long"
    assert clean_text("abcde", max_length=5, what="name") == "abcde"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Jo@Example.COM", "jo@example.com"), ("  jo.b@acme.co.uk ", "jo.b@acme.co.uk")],
)
def test_emails_are_lower_cased(text, expected):
    assert parse_email(text) == expected


@pytest.mark.parametrize(
    "text", ["jo", "jo@", "@acme.co.uk", "jo@@acme.co.uk", "jo bloggs@acme.co.uk"]
)
def test_bad_emails(text):
    assert code_of(parse_email, text) == "invalid_email"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ls1 4ap", "LS1 4AP"),
        ("LS14AP", "LS1 4AP"),
        (" sw1a  1aa ", "SW1A 1AA"),
        ("M1 1AE", "M1 1AE"),
    ],
)
def test_postcodes_are_normalised(text, expected):
    assert parse_postcode(text) == expected


@pytest.mark.parametrize("text", ["90210", "LS1", "ABCDE 1AA", "12345", "LS1 4A"])
def test_non_uk_postcodes_are_refused(text):
    assert code_of(parse_postcode, text) == "invalid_postcode"
