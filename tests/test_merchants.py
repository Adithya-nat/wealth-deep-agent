"""The normalization layer is only useful if it is boring and predictable."""

from __future__ import annotations

import pytest

from wealth_agent.merchants import (
    UNCATEGORIZED,
    clean_descriptor,
    merchant_key,
    normalize,
)
from wealth_agent.synthetic import build_transactions


@pytest.mark.parametrize(
    ("raw", "merchant", "category"),
    [
        ("SQ *BLUE BOTTLE COFFEE  OAKLAND", "Blue Bottle Coffee", "Dining"),
        ("AMZN Mktp US*2K4LM  AMZN.COM/BILL", "Amazon", "Shopping"),
        ("WHOLEFDS MTV#8821  MOUNTAIN VIEW", "Whole Foods", "Groceries"),
        ("NETFLIX.COM  LOS GATOS CA", "Netflix", "Entertainment"),
        ("AUTOPAY PAYMENT - THANK YOU", "Robinhood", "Payment"),
        # Precedence: iCloud must win over the generic Apple rule, or every
        # subscription lands in Shopping.
        ("ICLOUD+ STORAGE  APPLE", "Apple", "Software & Services"),
        ("APPLE STORE R4821  PALO ALTO CA", "Apple", "Shopping"),
    ],
)
def test_known_descriptors(raw: str, merchant: str, category: str) -> None:
    assert normalize(raw) == (merchant, category)


def test_unknown_merchant_falls_through_cleanly() -> None:
    merchant, category = normalize("UNKNOWN VENDOR LLC  #442  SAN JOSE CA")
    assert category == UNCATEGORIZED
    # Still groupable: the store number and city are gone, so two charges from
    # the same vendor at different branches collapse to one merchant.
    assert merchant == "UNKNOWN VENDOR LLC"


def test_branches_of_one_unknown_vendor_converge() -> None:
    a = merchant_key("UNKNOWN VENDOR LLC  #442  SAN JOSE CA")
    b = merchant_key("UNKNOWN VENDOR LLC  #118  PALO ALTO CA")
    assert a == b


def test_clean_descriptor_strips_stacked_suffixes() -> None:
    assert clean_descriptor("REI #4821  MOUNTAIN VIEW CA") == "REI"


def test_every_synthetic_transaction_categorizes_as_generated() -> None:
    """The fixtures and the rulebook must agree, or the eval baseline is fiction.

    This is the test that catches a rulebook edit silently reshaping every
    spending total in the demo.
    """
    mismatches = [
        row["description"]
        for row in build_transactions()
        if normalize(row["description"]) != (row["merchant"], row["category"])
    ]
    assert mismatches == []
