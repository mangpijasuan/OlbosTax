"""End-to-end taxpayer journey through the API.

Spec section 49 defines the MVP success criteria: create an account, enter
information, answer questions, see a calculated federal and Oklahoma return,
pay, sign, submit, and see filing status. This test walks that path.

It ends where the product honestly ends: at the filing gate, which refuses
because no tax rule set is certified and no authorized transmitter is
integrated. That refusal is the correct behaviour and is asserted, not worked
around.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.api.conftest import requires_db
from tests.fixtures.synthetic_taxpayers import synthetic_married_family_001

pytestmark = [requires_db, pytest.mark.integration]


def _taxpayer_payload():
    return {
        "first_name": "Marcus",
        "middle_initial": "T",
        "last_name": "Okafor",
        "ssn": "900-22-0002",
        "date_of_birth": "1986-09-03",
        "email": "marcus.okafor@example.com",
        "phone": "405-555-0199",
        "address": {
            "line1": "1200 N Robinson Ave",
            "line2": "Apt 14",
            "city": "Oklahoma City",
            "state": "OK",
            "zip_code": "73103",
        },
        "is_us_citizen_or_resident": True,
    }


def test_full_taxpayer_journey(client, registered_user, db_session):
    headers, user_id = registered_user

    # --- 1. Personal information ---------------------------------------
    response = client.put("/api/v1/taxpayers/me", json=_taxpayer_payload(), headers=headers)
    assert response.status_code == 200, response.text
    profile = response.json()
    # The SSN comes back masked. It went in as nine digits and must never
    # come back out.
    assert profile["ssn_masked"] == "***-**-0002"
    assert "900220002" not in response.text

    # --- 2. Start a return ----------------------------------------------
    tax_input = synthetic_married_family_001().model_dump(mode="python")
    tax_input = _jsonable(tax_input)

    response = client.post(
        "/api/v1/returns",
        json={"tax_year": 2025, "return_input": tax_input},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return_id = response.json()["return_id"]

    # --- 3. Save and calculate ------------------------------------------
    response = client.put(
        f"/api/v1/returns/{return_id}",
        json={"return_input": tax_input},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    computation = response.json()["computation"]

    federal = computation["federal"]
    oklahoma = computation["oklahoma"]
    assert Decimal(federal["adjusted_gross_income"]) > 0
    assert Decimal(federal["refund"]) > 0
    assert oklahoma is not None
    assert Decimal(oklahoma["oklahoma_adjusted_gross_income"]) > 0

    # The taxpayer is told the return cannot be filed, and why.
    assert computation["can_be_filed"] is False
    assert computation["rule_sets_certified"] is False
    finding_codes = {f["code"] for f in computation["findings"]}
    assert "rules.uncertified.federal" in finding_codes

    # --- 4. The calculation must be explainable -------------------------
    breakdown = computation["breakdown"]
    assert any(step["code"] == "FED_AGI" for step in breakdown)
    assert any(step["code"] == "OK_TAXABLE_INCOME" for step in breakdown)
    for step in breakdown:
        assert step["label"]

    response = client.get(f"/api/v1/returns/{return_id}/calculation", headers=headers)
    assert response.status_code == 200
    stored = response.json()
    assert stored["engine_version"]
    assert stored["federal_rule_version"]
    assert stored["trace"]["steps"]

    # --- 5. Payment ------------------------------------------------------
    response = client.post(
        "/api/v1/payments/checkout", json={"return_id": return_id}, headers=headers
    )
    assert response.status_code == 200, response.text
    checkout = response.json()
    assert checkout["amount_cents"] == 1499
    assert checkout["amount_display"] == "$14.99"
    assert "No additional fees" in checkout["description"]

    response = client.post(
        "/api/v1/payments/confirm",
        json={"payment_id": checkout["payment_id"], "processor_reference": "mock-ref-1"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "PAID"

    # --- 6. Finalize -- must be refused ----------------------------------
    # MFA is not enrolled, so this is refused for that reason first, which is
    # itself the correct behaviour for the step before signing.
    response = client.post(f"/api/v1/returns/{return_id}/finalize", headers=headers)
    assert response.status_code == 403
    assert "authenticator" in response.json()["detail"].lower()


def test_paid_return_still_cannot_be_filed(client, mfa_user):
    """Payment does not unlock filing while the rules are unverified.

    The most important assertion in the suite: a taxpayer who has paid, has
    MFA satisfied, and has passed every other gate must still not be able to
    transmit a return computed from unverified tax rules.
    """
    headers, _ = mfa_user
    client.put("/api/v1/taxpayers/me", json=_taxpayer_payload(), headers=headers)

    tax_input = _jsonable(synthetic_married_family_001().model_dump(mode="python"))
    return_id = client.post(
        "/api/v1/returns",
        json={"tax_year": 2025, "return_input": tax_input},
        headers=headers,
    ).json()["return_id"]

    checkout = client.post(
        "/api/v1/payments/checkout", json={"return_id": return_id}, headers=headers
    ).json()
    client.post(
        "/api/v1/payments/confirm",
        json={"payment_id": checkout["payment_id"], "processor_reference": "ref"},
        headers=headers,
    )

    # Finalizing is refused because the rule sets are not certified.
    response = client.post(f"/api/v1/returns/{return_id}/finalize", headers=headers)
    assert response.status_code == 409
    assert "not yet verified" in response.json()["detail"]

    # And submission is refused too, independently of finalize.
    response = client.post(
        "/api/v1/efile/submit", json={"return_id": return_id}, headers=headers
    )
    assert response.status_code in (403, 409, 503)


def test_filing_readiness_is_publicly_honest(client):
    """A visitor can learn before entering any data that filing is unavailable."""
    response = client.get("/api/v1/system/filing-readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["can_file"] is False
    assert len(body["blockers"]) == 2
    assert body["efile_channel"] == "MOCK"
    assert body["price"] == "$14.99"


def test_capability_matrix_is_reachable(client):
    """Regression: this literal path must not be captured by /{return_id}."""
    response = client.get("/api/v1/returns/capability-matrix")
    assert response.status_code == 200
    matrix = response.json()
    assert matrix["income.w2"]["level"] == "SUPPORTED"
    assert matrix["income.rental"]["level"] == "NOT_SUPPORTED"


def _jsonable(value):
    from datetime import date, datetime
    from decimal import Decimal as D
    from enum import Enum

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, D):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value
