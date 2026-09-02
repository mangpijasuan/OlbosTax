"""Security control tests.

These verify the controls actually behave as claimed. A redaction filter that
is installed but does not match the pattern in your logs is worse than none,
because it creates confidence without protection.
"""

import logging
from io import StringIO

import pytest

from olbostax_security import (
    REDACTED,
    DecryptionError,
    FieldCipher,
    LocalKeyring,
    LoginThrottle,
    RedactingFilter,
    SessionManager,
    create_totp_secret,
    generate_key,
    hash_ip,
    hash_password,
    install,
    needs_rehash,
    redact_mapping,
    redact_text,
    verify_password,
    verify_totp,
)

pytestmark = pytest.mark.security


class TestPasswordHashing:
    def test_hash_is_not_the_password(self):
        assert hash_password("correct horse battery staple") != "correct horse battery staple"

    def test_same_password_hashes_differently(self):
        """Distinct salts, so identical passwords are not identifiable in a dump."""
        assert hash_password("a-very-long-password") != hash_password("a-very-long-password")

    def test_verification_round_trip(self):
        stored = hash_password("a-very-long-password")
        assert verify_password(stored, "a-very-long-password")
        assert not verify_password(stored, "a-very-long-passwore")

    def test_missing_account_still_returns_false(self):
        assert not verify_password(None, "anything-at-all-here")

    def test_short_password_rejected(self):
        with pytest.raises(ValueError):
            hash_password("short")

    def test_absurdly_long_password_rejected(self):
        """An unbounded password is a CPU exhaustion vector against Argon2."""
        with pytest.raises(ValueError):
            hash_password("x" * 5000)

    def test_current_parameters_do_not_need_rehash(self):
        assert not needs_rehash(hash_password("a-very-long-password"))

    def test_garbage_hash_needs_rehash_rather_than_raising(self):
        assert needs_rehash("not-a-valid-argon2-hash")


class TestFieldEncryption:
    @pytest.fixture
    def cipher(self) -> FieldCipher:
        import base64

        keyring = LocalKeyring(
            keys={"1": base64.urlsafe_b64decode(generate_key())}, active="1"
        )
        return FieldCipher(keyring)

    def test_round_trip(self, cipher):
        token = cipher.encrypt("123456789", context="taxpayer:abc.ssn")
        assert cipher.decrypt(token, context="taxpayer:abc.ssn") == "123456789"

    def test_ciphertext_does_not_contain_plaintext(self, cipher):
        assert "123456789" not in cipher.encrypt("123456789", context="taxpayer:abc.ssn")

    def test_same_plaintext_encrypts_differently(self):
        """Deterministic encryption would let an attacker match equal SSNs."""
        import base64

        keyring = LocalKeyring(keys={"1": base64.urlsafe_b64decode(generate_key())}, active="1")
        cipher = FieldCipher(keyring)
        a = cipher.encrypt("123456789", context="taxpayer:abc.ssn")
        b = cipher.encrypt("123456789", context="taxpayer:abc.ssn")
        assert a != b

    def test_wrong_context_fails(self, cipher):
        """A ciphertext moved to another taxpayer's row must not decrypt."""
        token = cipher.encrypt("123456789", context="taxpayer:abc.ssn")
        with pytest.raises(DecryptionError):
            cipher.decrypt(token, context="taxpayer:xyz.ssn")

    def test_tampered_ciphertext_fails(self, cipher):
        token = cipher.encrypt("123456789", context="taxpayer:abc.ssn")
        head, tail = token.rsplit(".", 1)
        tampered = f"{head}.{'A' * len(tail)}"
        with pytest.raises(DecryptionError):
            cipher.decrypt(tampered, context="taxpayer:abc.ssn")

    def test_unknown_key_id_fails_cleanly(self, cipher):
        token = cipher.encrypt("123456789", context="c")
        _, _, nonce, ct = token.split(".")
        with pytest.raises(DecryptionError):
            cipher.decrypt(f"v1.99.{nonce}.{ct}", context="c")

    def test_local_keyring_refuses_production(self, monkeypatch):
        """A production fallback to an env-var keyring must fail loudly."""
        monkeypatch.setenv("OLBOSTAX_ENV", "production")
        with pytest.raises(RuntimeError, match="must not be used"):
            LocalKeyring.from_environment()

    def test_rotation_detection(self):
        import base64

        keys = {
            "1": base64.urlsafe_b64decode(generate_key()),
            "2": base64.urlsafe_b64decode(generate_key()),
        }
        old = FieldCipher(LocalKeyring(keys=keys, active="1"))
        new = FieldCipher(LocalKeyring(keys=keys, active="2"))
        token = old.encrypt("secret-value", context="c")
        assert new.needs_rotation(token)
        assert new.decrypt(token, context="c") == "secret-value"  # old key still readable


class TestRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "taxpayer ssn 123-45-6789 filed",
            "SSN: 123456789",
            "social_security_number=123-45-6789",
            "ITIN 900-11-2222",
        ],
    )
    def test_ssn_patterns_redacted(self, text):
        assert "123-45-6789" not in redact_text(text)
        assert "123456789" not in redact_text(text)

    def test_bank_details_redacted(self):
        out = redact_text("routing_number=103000648 account_number=000123456789")
        assert "103000648" not in out
        assert "000123456789" not in out

    def test_secrets_redacted(self):
        out = redact_text("password=hunter2seventeen api_key=sk_live_abc123")
        assert "hunter2seventeen" not in out
        assert "sk_live_abc123" not in out

    def test_jwt_redacted(self):
        token = "eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4"
        assert token not in redact_text(f"Authorization: Bearer {token}")

    def test_label_survives_so_logs_stay_useful(self):
        assert "ssn" in redact_text("ssn=123-45-6789").lower()

    def test_nested_structures_redacted(self):
        payload = {
            "taxpayer": {"ssn": "123-45-6789", "first_name": "Dana"},
            "documents": [{"account_number": "000123456789"}],
        }
        out = redact_mapping(payload)
        assert out["taxpayer"]["ssn"] == REDACTED
        assert out["taxpayer"]["first_name"] == "Dana"  # not sensitive
        assert out["documents"][0]["account_number"] == REDACTED

    def test_recursion_is_bounded(self):
        deep = current = {}
        for _ in range(50):
            current["next"] = {}
            current = current["next"]
        assert redact_mapping(deep)  # must not raise

    def test_filter_redacts_real_log_output(self):
        """End to end: a careless log call must not write an SSN to the stream."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        logger = logging.getLogger("test.redaction")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        logger.info("processing return for ssn=123-45-6789")
        logger.info("payload %s", {"ssn": "987-65-4321"})

        output = stream.getvalue()
        assert "123-45-6789" not in output
        assert "987-65-4321" not in output
        assert REDACTED in output

    def test_traceback_output_is_redacted(self):
        """Tracebacks are rendered by the formatter, after filters have run.

        A traceback inlines each frame's source line and the exception repr,
        so a database driver that quotes the offending row -- or a literal in
        the source -- reaches the output through a path the filter never sees.
        """
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("test.redaction.exc")
        logger.handlers = [handler]
        logger.propagate = False
        install(logger)

        try:
            raise ValueError("duplicate key for ssn 123-45-6789")
        except ValueError:
            logger.exception("insert failed")

        output = stream.getvalue()
        assert "Traceback" in output, "test must actually exercise a traceback"
        assert "123-45-6789" not in output
        assert REDACTED in output

    def test_install_is_idempotent(self):
        """Called twice, handlers must not accumulate duplicate filters."""
        logger = logging.getLogger("test.redaction.idempotent")
        logger.handlers = [logging.StreamHandler(StringIO())]
        install(logger)
        install(logger)
        filters = [f for f in logger.handlers[0].filters if isinstance(f, RedactingFilter)]
        assert len(filters) == 1


class TestSessions:
    @pytest.fixture
    def manager(self) -> SessionManager:
        return SessionManager("x" * 48)

    def test_issue_and_verify(self, manager):
        token = manager.issue("user-1", "sess-1", mfa_satisfied=True)
        claims = manager.verify(token.token)
        assert claims["sub"] == "user-1"
        assert claims["sid"] == "sess-1"
        assert "mfa" in claims["amr"]

    def test_tampered_token_rejected(self, manager):
        import jwt

        token = manager.issue("user-1", "sess-1", mfa_satisfied=True).token
        with pytest.raises(jwt.PyJWTError):
            manager.verify(token[:-4] + "AAAA")

    def test_token_signed_with_another_key_rejected(self, manager):
        import jwt

        other = SessionManager("y" * 48)
        forged = other.issue("attacker", "sess-x", mfa_satisfied=True).token
        with pytest.raises(jwt.PyJWTError):
            manager.verify(forged)

    def test_alg_none_token_rejected(self, manager):
        """The classic JWT attack: claim the token needs no signature."""
        import jwt

        forged = jwt.encode(
            {"sub": "attacker", "sid": "s", "exp": 9999999999, "iat": 1, "iss": "olbostax"},
            key="",
            algorithm="none",
        )
        with pytest.raises(jwt.PyJWTError):
            manager.verify(forged)

    def test_expired_token_rejected(self, manager):
        import jwt
        from datetime import timedelta

        short = SessionManager("x" * 48, access_ttl=timedelta(seconds=-1))
        with pytest.raises(jwt.ExpiredSignatureError):
            manager.verify(short.issue("u", "s", mfa_satisfied=True).token)

    def test_weak_secret_rejected(self):
        with pytest.raises(ValueError):
            SessionManager("tooshort")


class TestTOTP:
    def test_valid_code_accepted(self):
        import pyotp

        secret = create_totp_secret()
        assert verify_totp(secret, pyotp.TOTP(secret).now())

    def test_wrong_code_rejected(self):
        assert not verify_totp(create_totp_secret(), "000000")

    def test_non_numeric_rejected(self):
        assert not verify_totp(create_totp_secret(), "abcdef")

    def test_empty_rejected(self):
        assert not verify_totp(create_totp_secret(), "")


class TestLoginThrottle:
    def test_locks_after_max_attempts(self):
        throttle = LoginThrottle(max_attempts=3)
        for _ in range(2):
            locked, _ = throttle.record_failure("user@example.invalid")
            assert not locked
        locked, seconds = throttle.record_failure("user@example.invalid")
        assert locked
        assert seconds > 0

    def test_success_clears_the_counter(self):
        throttle = LoginThrottle(max_attempts=3)
        throttle.record_failure("k")
        throttle.record_success("k")
        assert not throttle.is_locked("k")[0]
        assert not throttle.record_failure("k")[0]

    def test_backoff_grows(self):
        throttle = LoginThrottle(max_attempts=1, base_lockout_seconds=10)
        _, first = throttle.record_failure("k")
        _, second = throttle.record_failure("k")
        assert second > first

    def test_backoff_is_capped(self):
        throttle = LoginThrottle(max_attempts=1, base_lockout_seconds=10, max_lockout_seconds=60)
        seconds = 0.0
        for _ in range(20):
            _, seconds = throttle.record_failure("k")
        assert seconds == 60

    def test_keys_are_independent(self):
        throttle = LoginThrottle(max_attempts=2)
        throttle.record_failure("a")
        throttle.record_failure("a")
        assert throttle.is_locked("a")[0]
        assert not throttle.is_locked("b")[0]


class TestIPHashing:
    def test_stable_for_same_input(self):
        assert hash_ip("203.0.113.7", "salt") == hash_ip("203.0.113.7", "salt")

    def test_differs_by_address(self):
        assert hash_ip("203.0.113.7", "salt") != hash_ip("203.0.113.8", "salt")

    def test_salt_prevents_rainbow_tables(self):
        """An unsalted hash over the IPv4 space is trivially reversible."""
        assert hash_ip("203.0.113.7", "salt-a") != hash_ip("203.0.113.7", "salt-b")

    def test_address_not_recoverable_from_output(self):
        assert "203.0.113.7" not in hash_ip("203.0.113.7", "salt")
