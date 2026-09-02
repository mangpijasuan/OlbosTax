"""Test fixtures for API integration tests.

Each test gets its own schema in a real PostgreSQL database rather than
SQLite. The schema relies on JSONB, partial indexes and check constraints that
SQLite does not implement, so testing against SQLite would verify a different
database from the one that runs in production -- and the constraints being
tested here are precisely the safety-critical ones.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "OLBOSTAX_TEST_DATABASE_URL",
    "postgresql+psycopg://olbostax:olbostax@127.0.0.1:5433/olbostax",
)


def _database_available() -> bool:
    try:
        create_engine(DATABASE_URL).connect().close()
    except Exception:
        return False
    return True


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason=f"PostgreSQL not reachable at {DATABASE_URL}",
)


@pytest.fixture(scope="function")
def db_session():
    from olbostax_api.models import Base

    engine = create_engine(DATABASE_URL)
    schema = "test_olbostax"
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {schema}"))

    scoped = create_engine(
        DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"}
    )
    Base.metadata.create_all(scoped)
    factory = sessionmaker(bind=scoped, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


@pytest.fixture
def client(db_session):
    from olbostax_api.app import create_app
    from olbostax_api.database import get_db

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def registered_user(client, db_session):
    """A verified, signed-in user. Returns (headers, user_id)."""
    from olbostax_api.models import User

    email = "synthetic.user@example.com"
    password = "a-sufficiently-long-test-password"

    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    db_session.commit()

    user = db_session.query(User).filter(User.email == email).one()
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user.id


@pytest.fixture
def mfa_user(client, db_session):
    """A signed-in user with MFA satisfied.

    Needed to reach the gates that sit *behind* the MFA requirement -- signing,
    finalizing and submitting. Without it, tests for those gates only ever
    prove that MFA is enforced, which is a different (and already covered)
    assertion.
    """
    import pyotp
    from olbostax_api.models import User
    from olbostax_api.services.crypto import get_cipher

    email = "mfa.user@example.com"
    password = "a-sufficiently-long-test-password"

    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    db_session.commit()
    user = db_session.query(User).filter(User.email == email).one()

    token = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/v1/auth/mfa/enroll", headers=headers)
    db_session.commit()
    secret = get_cipher().decrypt(
        user.mfa_secret_encrypted, context=f"user:{user.id}.mfa_secret"
    )
    client.post(
        "/api/v1/auth/mfa/confirm",
        json={"totp_code": pyotp.TOTP(secret).now()},
        headers=headers,
    )
    db_session.commit()

    # Re-login so the issued token carries the mfa claim.
    token = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "totp_code": pyotp.TOTP(secret).now()},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user.id
