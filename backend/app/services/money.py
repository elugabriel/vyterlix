"""VAT arithmetic, written once. Used by the file import and by manual entry, so a figure
typed in by hand and the same figure imported from a file always come out identical.

Amounts are pounds sterling. Revenue and costs are stored excluding VAT with the VAT
alongside, and net + VAT = gross exactly (ADR 0001 section 4). Everything here works on
positive amounts; callers apply the sign for refunds and credits.
"""

from decimal import Decimal

from app.services.value_parsers import round_pennies


class VatError(ValueError):
    """The VAT can't be worked out. `code` is stable; the message is for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def vat_split(
    amount: Decimal,
    *,
    includes_vat: bool,
    rate: Decimal | None = None,
    vat_amount: Decimal | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """(net, vat, gross) for a positive amount.

    VAT comes from `vat_amount` if given, else from `rate` (a percentage: 20, 5 or 0).
    `includes_vat` says whether `amount` already includes the VAT.
    """
    if vat_amount is not None:
        vat = vat_amount
        net = amount - vat if includes_vat else amount
        gross = amount if includes_vat else amount + vat
        if net < 0:
            raise VatError("vat_exceeds_amount", "The VAT is more than the amount.")
        return net, vat, gross
    if rate is None:
        raise VatError("vat_missing", "Give either the VAT amount or the VAT rate.")
    if includes_vat:
        net = round_pennies(amount / (1 + rate / 100))
        return net, amount - net, amount
    vat = round_pennies(amount * rate / 100)
    return amount, vat, amount + vat
