"""Authorization tests.

Spec section 34: a user must never reach another user's return by changing an
id in the URL. These tests create two real users with real returns and try,
directly, to cross the boundary.
"""

from __future__ import annotations

import pytest

from tests.api.conftest import requires_db
from tests.api.test_end_to_end import _jsonable, _taxpayer_payload
from tests.fixtures.synthetic_taxpayers import synthetic_taxpayer_001

pytestmark = [requires_db, pytest.mark.integration, pytest.mark.security]


def _make_user(client, db_session, email: str) -> tuple[dict, str]:
    from olbostax_api.models import User

    password = "a-sufficiently-long-test-password"
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    db_session.commit()
    user = db_session.query(User).filter(User.email == email).one()
    token = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user.id


def _make_return(client, headers) -> str:
    payload = dict(_taxpayer_payload())
    client.put("/api/v1/taxpayers/me", json=payload, headers=headers)
    tax_input = _jsonable(synthetic_taxpayer_001().model_dump(mode="python"))
    response = client.post(
        "/api/v1/returns", json={"tax_year": 2025, "return_input": tax_input}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["return_id"]


@pytest.fixture
def two_users(client, db_session):
    alice = _make_user(client, db_session, "alice@example.com")
    bob = _make_user(client, db_session, "bob@example.com")
    return alice, bob


class TestObjectLevelAuthorization:
    def test_cannot_read_another_users_return(self, client, two_users):
        (alice_headers, _), (bob_headers, _) = two_users
        alice_return = _make_return(client, alice_headers)

        response = client.get(f"/api/v1/returns/{alice_return}", headers=bob_headers)
        assert response.status_code == 404

    def test_cannot_modify_another_users_return(self, client, two_users):
        (alice_headers, _), (bob_headers, _) = two_users
        alice_return = _make_return(client, alice_headers)

        tax_input = _jsonable(synthetic_taxpayer_001().model_dump(mode="python"))
        response = client.put(
            f"/api/v1/returns/{alice_return}",
            json={"return_input": tax_input},
            headers=bob_headers,
        )
        assert response.status_code == 404

    def test_cannot_read_another_users_calculation(self, client, two_users):
        (alice_headers, _), (bob_headers, _) = two_users
        alice_return = _make_return(client, alice_headers)
        tax_input = _jsonable(synthetic_taxpayer_001().model_dump(mode="python"))
        client.put(
            f"/api/v1/returns/{alice_return}",
            json={"return_input": tax_input},
            headers=alice_headers,
        )

        response = client.get(
            f"/api/v1/returns/{alice_return}/calculation", headers=bob_headers
        )
        assert response.status_code == 404

    def test_cannot_pay_for_another_users_return(self, client, two_users):
        (alice_headers, _), (bob_headers, _) = two_users
        alice_return = _make_return(client, alice_headers)

        response = client.post(
            "/api/v1/payments/checkout",
            json={"return_id": alice_return},
            headers=bob_headers,
        )
        assert response.status_code == 404

    def test_cannot_submit_another_users_return(self, client, two_users):
        (alice_headers, _), (bob_headers, _) = two_users
        alice_return = _make_return(client, alice_headers)

        response = client.post(
            "/api/v1/efile/submit", json={"return_id": alice_return}, headers=bob_headers
        )
        assert response.status_code in (403, 404)

    def test_listing_shows_only_your_own_returns(self, client, two_users):
        (alice_headers, _), (bob_headers, _) = two_users
        _make_return(client, alice_headers)

        assert client.get("/api/v1/returns", headers=bob_headers).json() == []
        assert len(client.get("/api/v1/returns", headers=alice_headers).json()) == 1

    def test_missing_and_forbidden_are_indistinguishable(self, client, two_users):
        """Both must return 404.

        A 403 for "exists but not yours" versus 404 for "does not exist" lets
        an attacker map which ids are real, which is exactly what an IDOR
        probe measures.
        """
        (alice_headers, _), (bob_headers, _) = two_users
        alice_return = _make_return(client, alice_headers)

        forbidden = client.get(f"/api/v1/returns/{alice_return}", headers=bob_headers)
        nonexistent = client.get(
            "/api/v1/returns/00000000-0000-0000-0000-000000000000", headers=bob_headers
        )
        assert forbidden.status_code == nonexistent.status_code == 404
        assert forbidden.json() == nonexistent.json()


class TestAuthenticationRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/returns"),
            ("GET", "/api/v1/taxpayers/me"),
            ("PUT", "/api/v1/taxpayers/me"),
            ("GET", "/api/v1/auth/sessions"),
            ("POST", "/api/v1/payments/checkout"),
            ("POST", "/api/v1/efile/submit"),
            ("GET", "/api/v1/admin/dashboard"),
            ("GET", "/api/v1/admin/audit-logs"),
        ],
    )
    def test_protected_endpoints_reject_anonymous_requests(self, client, method, path):
        response = client.request(method, path, json={})
        assert response.status_code in (401, 403), f"{method} {path} was reachable"

    def test_garbage_token_rejected(self, client):
        response = client.get(
            "/api/v1/returns", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    def test_revoked_session_stops_working_immediately(self, client, registered_user):
        """A stateless token check alone would leave this session alive."""
        headers, _ = registered_user
        assert client.get("/api/v1/returns", headers=headers).status_code == 200

        client.post("/api/v1/auth/sessions/revoke-all", headers=headers)
        assert client.get("/api/v1/returns", headers=headers).status_code == 401


class TestAccountEnumeration:
    def test_registration_does_not_reveal_existing_accounts(self, client, db_session):
        payload = {
            "email": "enumerate@example.com",
            "password": "a-sufficiently-long-test-password",
        }
        first = client.post("/api/v1/auth/register", json=payload)
        db_session.commit()
        second = client.post("/api/v1/auth/register", json=payload)

        assert first.status_code == second.status_code
        assert first.json() == second.json()

    def test_login_failure_message_is_identical_for_unknown_and_wrong_password(
        self, client, db_session
    ):
        client.post(
            "/api/v1/auth/register",
            json={"email": "known@example.com", "password": "a-sufficiently-long-test-password"},
        )
        db_session.commit()

        wrong_password = client.post(
            "/api/v1/auth/login",
            json={"email": "known@example.com", "password": "wrong-but-long-enough-pw"},
        )
        unknown_account = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-but-long-enough-pw"},
        )
        assert wrong_password.status_code == unknown_account.status_code == 401
        assert wrong_password.json() == unknown_account.json()

    def test_password_reset_does_not_reveal_whether_the_account_exists(self, client):
        known = client.post(
            "/api/v1/auth/password/reset-request", json={"email": "known@example.com"}
        )
        unknown = client.post(
            "/api/v1/auth/password/reset-request", json={"email": "nobody@example.com"}
        )
        assert known.json() == unknown.json()


class TestSensitiveDataExposure:
    def test_ssn_never_returned_in_full(self, client, registered_user):
        headers, _ = registered_user
        client.put("/api/v1/taxpayers/me", json=_taxpayer_payload(), headers=headers)

        response = client.get("/api/v1/taxpayers/me", headers=headers)
        assert "900220002" not in response.text
        assert "900-22-0002" not in response.text
        assert response.json()["ssn_masked"] == "***-**-0002"

    def test_return_response_masks_identifiers(self, client, registered_user):
        headers, _ = registered_user
        return_id = _make_return(client, headers)

        response = client.get(f"/api/v1/returns/{return_id}", headers=headers)
        original = synthetic_taxpayer_001()
        assert original.taxpayer.ssn.reveal() not in response.text
        assert original.direct_deposit.account_number.reveal() not in response.text
        assert "***-**-" in response.text

    def test_security_headers_present(self, client):
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "no-store" in response.headers["Cache-Control"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Referrer-Policy"] == "no-referrer"
