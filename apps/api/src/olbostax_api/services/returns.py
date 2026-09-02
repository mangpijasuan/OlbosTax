"""Return lifecycle: versioning, calculation, and access control.

Two invariants are enforced here and nowhere else, so this is the module to
read when reasoning about whether a return can be tampered with:

**Every lookup is scoped by owner.** There is no function in this module that
fetches a return by id alone. ``user_id`` is a required argument on every
loader, so an IDOR is not something a caller can introduce by forgetting a
check -- they would have to fabricate a user id, which is a different and much
more visible kind of mistake.

**Finalized versions are never mutated.** ``mutable_version`` returns a draft
or raises. Code that wants to change a submitted return gets an exception
telling it to create a new version, which is the correct behaviour, rather
than silently editing filed history.
"""

from __future__ import annotations

from datetime import UTC, datetime

from olbostax_engine import TaxEngine, load_rule_set
from olbostax_schema import ReturnStatus, TaxComputation, TaxReturnInput
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TaxCalculation, TaxReturn, TaxReturnVersion
from . import audit
from .crypto import decrypt_return_input, encrypt_return_input

__all__ = [
    "ReturnNotFound",
    "ReturnImmutable",
    "get_return",
    "get_version",
    "mutable_version",
    "create_return",
    "create_version",
    "save_input",
    "calculate",
    "latest_calculation",
    "finalize",
]


class ReturnNotFound(LookupError):
    """The return does not exist, or does not belong to this user.

    Deliberately one exception for both cases. Distinguishing them would let an
    attacker enumerate which return ids exist by comparing responses, which is
    the information an IDOR probe is looking for.
    """


class ReturnImmutable(RuntimeError):
    """An attempt to modify a finalized return version."""


# ---------------------------------------------------------------------------
# Loading -- always scoped by owner
# ---------------------------------------------------------------------------


def get_return(db: Session, return_id: str, *, user_id: str) -> TaxReturn:
    result = db.execute(
        select(TaxReturn).where(
            TaxReturn.id == return_id, TaxReturn.user_id == user_id
        )
    ).scalar_one_or_none()
    if result is None:
        raise ReturnNotFound(return_id)
    return result


def get_version(db: Session, version_id: str, *, user_id: str) -> TaxReturnVersion:
    """Load a version, verifying ownership through its parent return."""
    result = db.execute(
        select(TaxReturnVersion)
        .join(TaxReturn, TaxReturnVersion.tax_return_id == TaxReturn.id)
        .where(TaxReturnVersion.id == version_id, TaxReturn.user_id == user_id)
    ).scalar_one_or_none()
    if result is None:
        raise ReturnNotFound(version_id)
    return result


def mutable_version(db: Session, version_id: str, *, user_id: str) -> TaxReturnVersion:
    """Load a version for editing, refusing if it is finalized."""
    version = get_version(db, version_id, user_id=user_id)
    if ReturnStatus(version.status).is_finalized:
        raise ReturnImmutable(
            f"version {version.version_number} is {version.status} and cannot be "
            "changed; create a new version instead"
        )
    return version


def current_version(db: Session, tax_return: TaxReturn) -> TaxReturnVersion | None:
    if tax_return.current_version_id is None:
        return None
    return db.get(TaxReturnVersion, tax_return.current_version_id)


def list_returns(db: Session, *, user_id: str) -> list[TaxReturn]:
    return list(
        db.execute(
            select(TaxReturn)
            .where(TaxReturn.user_id == user_id)
            .order_by(TaxReturn.tax_year.desc())
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_return(
    db: Session,
    *,
    user_id: str,
    taxpayer_id: str,
    tax_year: int,
    initial_input: TaxReturnInput,
    files_federal: bool = True,
    files_oklahoma: bool = True,
    ip: str | None = None,
) -> tuple[TaxReturn, TaxReturnVersion]:
    """Start a return and its first draft version."""
    tax_return = TaxReturn(
        user_id=user_id,
        taxpayer_id=taxpayer_id,
        tax_year=tax_year,
        files_federal=files_federal,
        files_oklahoma=files_oklahoma,
    )
    db.add(tax_return)
    db.flush()

    version = TaxReturnVersion(
        tax_return_id=tax_return.id,
        version_number=1,
        status=ReturnStatus.DRAFT.value,
        return_input=_serialize(initial_input),
    )
    db.add(version)
    db.flush()

    tax_return.current_version_id = version.id
    audit.record(
        db,
        audit.AuditEvent.TAX_RETURN_CREATED,
        user_id=user_id,
        tax_return_id=tax_return.id,
        ip=ip,
        metadata={"tax_year": tax_year},
    )
    return tax_return, version


def create_version(
    db: Session,
    tax_return: TaxReturn,
    *,
    user_id: str,
    reason: str,
    ip: str | None = None,
) -> TaxReturnVersion:
    """Fork a new draft version from the current one.

    Used after a rejection, or when a taxpayer wants to change a return they
    have already submitted. The previous version is marked SUPERSEDED rather
    than deleted: what was transmitted must remain reconstructible.
    """
    previous = current_version(db, tax_return)
    if previous is None:
        raise ReturnNotFound(tax_return.id)

    next_number = (
        db.execute(
            select(TaxReturnVersion.version_number)
            .where(TaxReturnVersion.tax_return_id == tax_return.id)
            .order_by(TaxReturnVersion.version_number.desc())
            .limit(1)
        ).scalar_one()
        + 1
    )

    version = TaxReturnVersion(
        tax_return_id=tax_return.id,
        version_number=next_number,
        status=ReturnStatus.DRAFT.value,
        return_input=dict(previous.return_input),
        supersedes_version_id=previous.id,
    )
    db.add(version)
    db.flush()

    previous.status = ReturnStatus.SUPERSEDED.value
    previous.superseded_at = datetime.now(UTC)
    tax_return.current_version_id = version.id

    audit.record(
        db,
        audit.AuditEvent.TAX_RETURN_VERSION_CREATED,
        user_id=user_id,
        tax_return_id=tax_return.id,
        target_type="tax_return_version",
        target_id=version.id,
        ip=ip,
        metadata={"version_number": next_number, "reason": reason},
    )
    return version


# ---------------------------------------------------------------------------
# Editing and calculation
# ---------------------------------------------------------------------------


def save_input(
    db: Session,
    version: TaxReturnVersion,
    tax_input: TaxReturnInput,
    *,
    user_id: str,
    ip: str | None = None,
) -> TaxReturnVersion:
    """Persist the interview state onto a draft version."""
    if ReturnStatus(version.status).is_finalized:
        raise ReturnImmutable(f"version {version.id} is {version.status}")

    version.return_input = _serialize(tax_input)
    audit.record(
        db,
        audit.AuditEvent.TAX_RETURN_UPDATED,
        user_id=user_id,
        tax_return_id=version.tax_return_id,
        target_type="tax_return_version",
        target_id=version.id,
        ip=ip,
    )
    return version


def calculate(
    db: Session,
    version: TaxReturnVersion,
    *,
    user_id: str,
    ip: str | None = None,
) -> tuple[TaxComputation, TaxCalculation]:
    """Run the engine over a version and store the snapshot.

    Every run is stored rather than overwriting the previous one. The
    calculation history is what answers "my refund changed, what did I do?",
    and it costs a row.
    """
    # Decrypt before validating: the stored document holds ciphertext in the
    # sensitive fields, and the engine needs the real identifiers.
    tax_input = decrypt_return_input(version.return_input)
    engine = TaxEngine(
        federal_rules=load_rule_set("federal", tax_input.tax_year),
        oklahoma_rules=(
            load_rule_set("oklahoma", tax_input.tax_year)
            if tax_input.files_oklahoma
            else None
        ),
    )
    computation = engine.compute(tax_input)

    federal = computation.federal
    oklahoma = computation.oklahoma
    snapshot = TaxCalculation(
        version_id=version.id,
        engine_version=computation.engine_version,
        federal_rule_version=computation.federal_rule_version,
        oklahoma_rule_version=computation.oklahoma_rule_version,
        computed_at=computation.computed_at,
        federal_agi=federal.adjusted_gross_income if federal else None,
        federal_total_tax=federal.total_tax if federal else None,
        federal_refund=federal.refund if federal else None,
        federal_amount_owed=federal.amount_owed if federal else None,
        oklahoma_total_tax=oklahoma.total_tax if oklahoma else None,
        oklahoma_refund=oklahoma.refund if oklahoma else None,
        oklahoma_amount_owed=oklahoma.amount_owed if oklahoma else None,
        federal_result=federal.model_dump(mode="json") if federal else None,
        oklahoma_result=oklahoma.model_dump(mode="json") if oklahoma else None,
        trace=computation.trace.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in computation.findings],
        rule_sets_certified=computation.rule_sets_certified_for_filing,
    )
    db.add(snapshot)

    audit.record(
        db,
        audit.AuditEvent.TAX_RETURN_RECALCULATED,
        user_id=user_id,
        tax_return_id=version.tax_return_id,
        target_type="tax_return_version",
        target_id=version.id,
        ip=ip,
        metadata={
            "engine_version": computation.engine_version,
            "federal_refund": str(federal.refund) if federal else None,
            "oklahoma_refund": str(oklahoma.refund) if oklahoma else None,
        },
    )
    return computation, snapshot


def latest_calculation(db: Session, version: TaxReturnVersion) -> TaxCalculation | None:
    return db.execute(
        select(TaxCalculation)
        .where(TaxCalculation.version_id == version.id)
        .order_by(TaxCalculation.computed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def finalize(
    db: Session,
    version: TaxReturnVersion,
    computation: TaxComputation,
    *,
    user_id: str,
    ip: str | None = None,
) -> TaxReturnVersion:
    """Mark a version ready for filing.

    Refuses when the engine says the return cannot be filed -- uncertified
    rule sets, or a situation the engine could not compute. Checked here as
    well as in the e-file provider because this is where the taxpayer is told
    "your return is ready", and telling them that about a return that cannot
    be filed wastes their time and their trust.
    """
    if ReturnStatus(version.status).is_finalized:
        raise ReturnImmutable(f"version {version.id} is already {version.status}")

    if not computation.can_be_filed:
        reasons = [f.title for f in computation.blocking_findings]
        # An uncertified rule set already raises a per-jurisdiction finding, so
        # a generic line is only added when nothing else has said it. Otherwise
        # the taxpayer is told the same thing three times, which reads as a bug
        # and buries any finding that is actually about their return.
        if not computation.rule_sets_certified_for_filing and not reasons:
            reasons.append("the tax rules for this year are not yet verified")
        raise ReturnImmutable(
            "this return cannot be marked ready for filing: " + "; ".join(reasons)
        )

    version.status = ReturnStatus.READY_FOR_FILING.value
    version.finalized_at = datetime.now(UTC)
    audit.record(
        db,
        audit.AuditEvent.TAX_RETURN_FINALIZED,
        user_id=user_id,
        tax_return_id=version.tax_return_id,
        target_type="tax_return_version",
        target_id=version.id,
        ip=ip,
    )
    return version


def _serialize(tax_input: TaxReturnInput) -> dict:
    """Serialize return input for storage, preserving sensitive values.

    Sensitive fields are encrypted on the way in. Every reader of
    ``version.return_input`` must go through ``decrypt_return_input`` -- there
    is no code path that should validate the stored document directly, because
    the sensitive fields will not parse as their value types.
    """
    return encrypt_return_input(tax_input)
