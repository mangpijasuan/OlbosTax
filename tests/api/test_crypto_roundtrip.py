"""Storage encryption round-trip tests.

The property that matters: a return survives storage byte-for-byte on every
field the engine reads, while the identifiers a breach would expose are
ciphertext in the stored document.
"""

import pytest

from olbostax_api.services.crypto import (
    SENSITIVE_PATHS,
    decrypt_return_input,
    encrypt_return_input,
)
from olbostax_engine import compute
from tests.fixtures.synthetic_taxpayers import ALL_SYNTHETIC_TAXPAYERS


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_round_trip_preserves_the_return(name):
    original = ALL_SYNTHETIC_TAXPAYERS[name]()
    restored = decrypt_return_input(encrypt_return_input(original))

    assert restored.taxpayer.ssn.reveal() == original.taxpayer.ssn.reveal()
    assert restored.filing_status is original.filing_status
    assert restored.tax_year == original.tax_year
    assert len(restored.dependents) == len(original.dependents)
    if original.direct_deposit:
        assert (
            restored.direct_deposit.account_number.reveal()
            == original.direct_deposit.account_number.reveal()
        )


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_round_trip_preserves_the_computed_result(name):
    """The real test: storage must not change a single dollar of the answer."""
    original = ALL_SYNTHETIC_TAXPAYERS[name]()
    restored = decrypt_return_input(encrypt_return_input(original))

    before, after = compute(original), compute(restored)
    assert before.federal == after.federal
    assert before.oklahoma == after.oklahoma


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_stored_document_contains_no_plaintext_identifiers(name):
    """What a database compromise or leaked backup would actually yield."""
    original = ALL_SYNTHETIC_TAXPAYERS[name]()
    stored = repr(encrypt_return_input(original))

    assert original.taxpayer.ssn.reveal() not in stored
    if original.spouse:
        assert original.spouse.ssn.reveal() not in stored
    for dependent in original.dependents:
        assert dependent.ssn.reveal() not in stored
    if original.direct_deposit:
        assert original.direct_deposit.account_number.reveal() not in stored
        assert original.direct_deposit.routing_number.reveal() not in stored


def test_money_survives_as_exact_decimals():
    """A wage amount must not round-trip through a float."""
    from decimal import Decimal

    original = ALL_SYNTHETIC_TAXPAYERS["SyntheticTaxpayer001"]()
    stored = encrypt_return_input(original)
    assert stored["income"]["w2s"][0]["box1_wages"] == "58240.12"

    restored = decrypt_return_input(stored)
    assert restored.income.w2s[0].box1_wages == Decimal("58240.12")


def test_every_sensitive_path_is_actually_encrypted():
    """Guards against a path being listed but silently not applied."""
    original = ALL_SYNTHETIC_TAXPAYERS["SyntheticMarriedFamily001"]()
    stored = encrypt_return_input(original)

    assert stored["taxpayer"]["ssn"].startswith("enc:")
    assert stored["spouse"]["ssn"].startswith("enc:")
    for dependent in stored["dependents"]:
        assert dependent["ssn"].startswith("enc:")
    assert stored["direct_deposit"]["account_number"].startswith("enc:")
    assert stored["direct_deposit"]["routing_number"].startswith("enc:")
    assert len(SENSITIVE_PATHS) == 5


def test_non_sensitive_fields_stay_readable():
    """Selective encryption keeps the document useful for diagnostics."""
    original = ALL_SYNTHETIC_TAXPAYERS["SyntheticTaxpayer001"]()
    stored = encrypt_return_input(original)

    assert stored["taxpayer"]["first_name"] == "Dana"
    assert stored["filing_status"] == "SINGLE"
    assert stored["tax_year"] == 2025
