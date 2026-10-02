"""VAT arithmetic shared by imports and manual entry."""

from decimal import Decimal

import pytest

from app.services.money import VatError, vat_split

D = Decimal


@pytest.mark.parametrize(
    ("amount", "includes", "rate", "net", "vat", "gross"),
    [
        ("12.00", True, "20", "10.00", "2.00", "12.00"),
        ("10.00", False, "20", "10.00", "2.00", "12.00"),
        ("9.99", True, "20", "8.33", "1.66", "9.99"),  # 8.325 rounds half up
        ("8.33", False, "20", "8.33", "1.67", "10.00"),  # 1.666 rounds up
        ("10.00", True, "5", "9.52", "0.48", "10.00"),
        ("10.00", True, "0", "10.00", "0.00", "10.00"),
        ("100", False, "5", "100", "5.00", "105.00"),
        ("0.10", False, "20", "0.10", "0.02", "0.12"),
    ],
)
def test_split_by_rate(amount, includes, rate, net, vat, gross):
    got = vat_split(D(amount), includes_vat=includes, rate=D(rate))
    assert got == (D(net), D(vat), D(gross))
    assert got[0] + got[1] == got[2]


@pytest.mark.parametrize(
    ("amount", "includes", "vat", "net", "gross"),
    [("12.00", True, "2.00", "10.00", "12.00"), ("10.00", False, "2.00", "10.00", "12.00")],
)
def test_split_by_a_given_vat_amount(amount, includes, vat, net, gross):
    got = vat_split(D(amount), includes_vat=includes, vat_amount=D(vat))
    assert got == (D(net), D(vat), D(gross))


def test_a_given_vat_amount_beats_a_rate():
    got = vat_split(D("12.00"), includes_vat=True, rate=D("5"), vat_amount=D("2.00"))
    assert got == (D("10.00"), D("2.00"), D("12.00"))


def test_vat_larger_than_the_total_is_refused():
    with pytest.raises(VatError) as caught:
        vat_split(D("1.00"), includes_vat=True, vat_amount=D("2.00"))
    assert caught.value.code == "vat_exceeds_amount"


def test_some_vat_information_is_required():
    with pytest.raises(VatError) as caught:
        vat_split(D("1.00"), includes_vat=True)
    assert caught.value.code == "vat_missing"


def test_vat_equal_to_the_total_leaves_nothing_net():
    got = vat_split(D("2.00"), includes_vat=True, vat_amount=D("2.00"))
    assert got == (D("0"), D("2.00"), D("2.00"))
