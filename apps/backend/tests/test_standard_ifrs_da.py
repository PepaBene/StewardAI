"""Depreciation and amortization from the halves IFRS filers tag apart."""

from decimal import Decimal

from steward.company.standard import _depreciation_amortization

D = Decimal


def test_a_filed_total_depreciation_is_used_as_is() -> None:
    # TSMC: one depreciation total (right-of-use included) and amortisation.
    figures = {
        "depreciation": D(653),
        "depreciation_right_of_use": D(4),
        "amortization_of_intangibles": D(9),
    }
    assert _depreciation_amortization(figures.get) == (
        D(662),
        "depreciation + amortization of intangibles",
    )


def test_ppe_and_right_of_use_depreciation_are_added_when_no_total_is_filed() -> None:
    # AstraZeneca FY2025: 879 PP&E + 404 right-of-use + 4,207 intangibles (USD m).
    figures = {
        "depreciation_ppe": D(879),
        "depreciation_right_of_use": D(404),
        "amortization_of_intangibles": D(4207),
    }
    value, how = _depreciation_amortization(figures.get)
    assert value == D(5490)
    assert how == "depreciation of PP&E and right-of-use assets + amortization of intangibles"


def test_nothing_without_any_depreciation() -> None:
    assert _depreciation_amortization({"amortization_of_intangibles": D(1)}.get) == (None, "")
