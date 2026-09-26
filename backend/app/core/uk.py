"""UK-first helpers (see the standing rule in docs/CHECKLIST.md).

One place for UK formats, so every feature normalises postcodes, VAT numbers and dates
the same way. The database enforces the final shape with CHECK constraints.
"""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")
CURRENCY = "GBP"
COUNTRY = "GB"

_POSTCODE = re.compile(r"^([A-Z]{1,2}[0-9][A-Z0-9]?) ?([0-9][A-Z]{2})$")
_VAT = re.compile(r"^(GB)?([0-9]{9}|[0-9]{12})$")
_DAYS_IN_MONTH = {2: 28, 4: 30, 6: 30, 9: 30, 11: 30}


def today_uk() -> date:
    """Today's date in the UK (not the server's time zone)."""
    return datetime.now(UK_TZ).date()


def normalise_postcode(value: str) -> str:
    """'sw1a1aa' / ' SW1A  1AA ' -> 'SW1A 1AA'. Raises ValueError if not a UK postcode."""
    compact = re.sub(r"\s+", "", value).upper()
    if compact == "GIR0AA":
        return "GIR 0AA"
    match = _POSTCODE.match(compact)
    if not match:
        raise ValueError("Enter a valid UK postcode, for example SW1A 1AA")
    return f"{match.group(1)} {match.group(2)}"


def normalise_vat_number(value: str) -> str:
    """'gb 123 4567 89' / '123456789' -> 'GB123456789'. Raises ValueError if not GB format."""
    compact = re.sub(r"[\s.-]+", "", value).upper()
    match = _VAT.match(compact)
    if not match:
        raise ValueError("Enter a UK VAT number, for example GB 123 4567 89")
    return f"GB{match.group(2)}"


def is_valid_day_of_month(month: int, day: int) -> bool:
    """True if this day exists every year (so 29 February is not accepted)."""
    return 1 <= month <= 12 and 1 <= day <= _DAYS_IN_MONTH.get(month, 31)
