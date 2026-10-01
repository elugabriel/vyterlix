"""Turning the text in a spreadsheet cell into a proper value, the UK way.

Strict on purpose: a cell that can't be read with certainty is reported to the person, never
guessed, because a guess goes straight into their accounts.

- Dates are day first: 28/09/2026, 28-9-26, 28.09.2026, 28 Sep 2026, or ISO 2026-09-28.
  A US-style 09/28/2026 is refused (month 28), not silently reinterpreted.
- Money is pounds sterling with a full stop for pence and commas only as thousands
  separators: 1234.50, £1,234.50, (12.50) and -12.50 for negatives. A comma used for pence
  ("4,50") is refused rather than read as 450.
- VAT rates are the UK ones: 20, 5 or 0, with or without a % sign.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from email_validator import EmailNotValidError, validate_email

from app.core.uk import normalise_postcode, today_uk

PENNY = Decimal("0.01")
MAX_MONEY = Decimal("1000000000000")  # 1e12
MAX_QUANTITY = Decimal("1000000000")  # 1e9
EARLIEST_DATE = date(1990, 1, 1)
UK_VAT_RATES = (Decimal("0"), Decimal("5"), Decimal("20"))


class CellError(ValueError):
    """A cell that can't be used. `code` is stable for grouping; the message is for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _shown(text: str) -> str:
    text = text.strip()
    return f"'{text[:40]}…'" if len(text) > 40 else f"'{text}'"


def round_pennies(value: Decimal) -> Decimal:
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


# --- dates -----------------------------------------------------------

_ISO = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_UK = re.compile(
    r"^(\d{1,2})[/.\- ](\d{1,2})[/.\- ](\d{2}|\d{4})(?:[ T]+\d{1,2}:\d{2}(?::\d{2})?)?$"
)
_TEXT = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[ \-/]([A-Za-z]{3,9})\.?,?[ \-/](\d{2}|\d{4})$")
_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}


def _year(text: str) -> int:
    year = int(text)
    if len(text) == 2:
        return 2000 + year if year < 70 else 1900 + year
    return year


def parse_date(text: str, *, today: date | None = None) -> date:
    text = text.strip()
    today = today or today_uk()
    if match := _ISO.match(text):
        year, month, day = int(match[1]), int(match[2]), int(match[3])
    elif match := _UK.match(text):
        day, month, year = int(match[1]), int(match[2]), _year(match[3])
        if month > 12 >= day:
            raise CellError(
                "invalid_date",
                f"{_shown(text)} isn't a UK date: dates must be day/month/year, "
                f"and there is no month {month}.",
            )
    elif match := _TEXT.match(text):
        month = _MONTHS.get(match[2].lower(), 0)
        day, year = int(match[1]), _year(match[3])
        if not month:
            raise CellError("invalid_date", f"{_shown(text)} isn't a date I can read.")
    else:
        raise CellError(
            "invalid_date",
            f"{_shown(text)} isn't a date I can read. Use day/month/year, e.g. 28/09/2026.",
        )
    try:
        parsed = date(year, month, day)
    except ValueError:
        raise CellError("invalid_date", f"{_shown(text)} isn't a real date.") from None
    if parsed < EARLIEST_DATE:
        raise CellError("date_out_of_range", f"{_shown(text)} is before 1990, which looks wrong.")
    if parsed > today + timedelta(days=1):
        raise CellError("date_out_of_range", f"{_shown(text)} is in the future, which looks wrong.")
    return parsed


# --- money, quantities, rates ----------------------------------------

_NUMBER = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?$")
_WRONG_CURRENCY = re.compile(r"[$€¥]|\b(usd|eur|euro|dollars?)\b", re.IGNORECASE)
_DECIMAL_COMMA = re.compile(r"^\d+,\d{1,2}$")


def _decimal(text: str, *, what: str, max_places: int, limit: Decimal, currency: bool) -> Decimal:
    original = text
    text = text.replace(" ", " ").strip()
    if currency and _WRONG_CURRENCY.search(text):
        raise CellError(
            "wrong_currency", f"{_shown(original)} isn't in pounds sterling. Only £ is supported."
        )
    negative = False
    if text.startswith("(") and text.endswith(")"):  # accounting style: (12.50)
        negative, text = True, text[1:-1].strip()
    if currency:
        text = re.sub(r"(?i)^(£|gbp)\s*|\s*(£|gbp)$", "", text)
    for sign in ("-", "−"):
        if text.startswith(sign):
            negative, text = not negative, text[1:].strip()
        elif text.endswith(sign):
            negative, text = not negative, text[:-1].strip()
    if text.startswith("+"):
        text = text[1:].strip()
    if currency:
        text = re.sub(r"(?i)^(£|gbp)\s*", "", text)  # "-£5.00"
    if _DECIMAL_COMMA.match(text):
        raise CellError(
            "invalid_amount",
            f"{_shown(original)} uses a comma for pence. Use a full stop, e.g. 4.50.",
        )
    if not text or text == "." or not _NUMBER.match(text):
        raise CellError(
            "invalid_amount",
            f"{_shown(original)} isn't a valid {what}. Use numbers like 1234.50"
            + (" or £1,234.50." if currency else "."),
        )
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the pattern above rules this out
        raise CellError("invalid_amount", f"{_shown(original)} isn't a valid {what}.") from None
    if -value.as_tuple().exponent > max_places:
        raise CellError(
            "too_many_decimals",
            f"{_shown(original)} has more than {max_places} decimal places.",
        )
    if value >= limit:
        raise CellError("amount_too_large", f"{_shown(original)} is too large.")
    return -value if negative else value


def parse_money(text: str) -> Decimal:
    """Pounds, up to 4 decimal places (unit costs can be fractions of a penny)."""
    return _decimal(text, what="amount", max_places=4, limit=MAX_MONEY, currency=True)


def parse_quantity(text: str) -> Decimal:
    return _decimal(text, what="quantity", max_places=4, limit=MAX_QUANTITY, currency=False)


def parse_vat_rate(text: str) -> Decimal:
    cleaned = text.strip()
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()  # one percent sign, not several
    try:
        value = _decimal(
            cleaned, what="VAT rate", max_places=2, limit=Decimal(1000), currency=False
        )
    except CellError:
        value = None
    if value is None or value not in UK_VAT_RATES:
        raise CellError("invalid_vat_rate", f"{_shown(text)} isn't a UK VAT rate. Use 20, 5 or 0.")
    return value.quantize(Decimal("1"))


# --- text ------------------------------------------------------------


def clean_text(text: str, *, max_length: int, what: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) > max_length:
        raise CellError("too_long", f"The {what} is longer than {max_length} characters.")
    return cleaned


def parse_email(text: str) -> str:
    try:
        return validate_email(text.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise CellError("invalid_email", f"{_shown(text)} isn't a valid email address.") from None


def parse_postcode(text: str) -> str:
    try:
        return normalise_postcode(text)
    except ValueError:
        raise CellError(
            "invalid_postcode", f"{_shown(text)} isn't a UK postcode, e.g. LS1 4AP."
        ) from None
