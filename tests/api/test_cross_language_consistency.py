"""Checks that duplicated constants in the web app match their Python source.

The web app cannot import Python enums, so a few values are necessarily
written twice. Each duplicate is a place where the two can drift silently: the
API keeps enforcing one rule while the UI acts on another, and the symptom
reaches a taxpayer rather than a test.

These are cheap to check and the failure is specific enough to fix in a
minute, which is a better trade than trusting two lists to be kept in step by
hand.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from olbostax_schema import ReturnStatus

WEB = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"

pytestmark = pytest.mark.security


def _extract_string_set(source: str, name: str) -> set[str]:
    match = re.search(rf"{name} = new Set\(\[(.*?)\]\)", source, re.S)
    assert match, f"could not find `{name}` in the web source"
    return set(re.findall(r'"([A-Z_]+)"', match.group(1)))


def test_finalized_statuses_match_the_engine() -> None:
    """The UI must agree with the API about which returns are read-only.

    If the UI's list is missing a status, the review page tries to save a
    finalized return, the API rejects it with 409, and the taxpayer is shown an
    error for the ordinary act of looking at a return they already filed.
    """
    page = WEB / "app" / "returns" / "[id]" / "review" / "page.tsx"
    from_web = _extract_string_set(page.read_text(), "FINALIZED_STATUSES")
    from_engine = {status.value for status in ReturnStatus if status.is_finalized}

    assert from_web == from_engine, (
        "apps/web review page and ReturnStatus.is_finalized disagree; "
        f"web has {sorted(from_web)}, engine has {sorted(from_engine)}"
    )


def test_price_is_stated_once_in_the_web_app() -> None:
    """The price a taxpayer is shown must come from the server.

    The landing page quotes $14.99 as marketing copy, which is fine. What must
    not happen is a checkout screen computing or hard-coding an amount of its
    own: the charge is decided by the API, and a second source of truth is how
    a page ends up promising one figure while another is billed.
    """
    checkout_pages = [
        path
        for path in WEB.rglob("*.tsx")
        if "amount_cents" in path.read_text() or "amount_display" in path.read_text()
    ]
    for path in checkout_pages:
        source = path.read_text()
        assert "1499" not in source, (
            f"{path.relative_to(WEB)} hard-codes the price; it must render the "
            "amount the API returned"
        )
