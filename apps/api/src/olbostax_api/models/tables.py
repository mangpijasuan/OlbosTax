"""The OlbosTax database schema.

Three rules govern the design and they are worth stating before the tables:

**Finalized returns are immutable.** A return version that has been submitted
is never updated. Corrections create a new version. The database enforces this
with a trigger-equivalent check in the service layer plus a status constraint,
because "we won't modify it" as a code convention lasts until the first
urgent support request.

**Nothing legally required cascades away.** Deleting a user must not delete
their filed returns, their payments, or the audit log of what happened to
them. Retention obligations outlive an account deletion request, so those
relationships use ``RESTRICT`` and account deletion is a status change plus
data minimisation, not a ``DELETE``.

**Sensitive columns hold ciphertext.** SSNs and bank details are encrypted in
the service layer with a per-row context. A read replica, a SQL injection, or
a restored backup yields ciphertext.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, EncryptedText, TimestampMixin, uuid_pk

# Money columns. 14 digits with 2 decimal places holds any individual return
# amount with room to spare, and NUMERIC is exact -- a FLOAT column would
# reintroduce, at the storage layer, precisely the error the engine avoids.
Money = Numeric(14, 2)


# ===========================================================================
# Identity and access
# ===========================================================================


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mfa_secret_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_recovery_hashes: Mapped[list | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Deletion is a status change plus data minimisation. The row survives
    # because filed returns reference it and must be retained.
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    taxpayers: Mapped[list[Taxpayer]] = relationship(back_populates="user")
    sessions: Mapped[list[Session]] = relationship(back_populates="user")

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED','DELETION_REQUESTED','ANONYMIZED')",
            name="status_valid",
        ),
    )


class Session(Base, TimestampMixin):
    """An authenticated session. Rows exist so sessions can be revoked."""

    __tablename__ = "sessions"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_satisfied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Never the address itself: a salted hash answers "same place?" without
    # storing personal data that carries its own retention obligations.
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    device_label: Mapped[str | None] = mapped_column(String(128))

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"

    id: Mapped[str] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('SUPER_ADMIN','TAX_ADMIN','SUPPORT','SECURITY_ADMIN','FINANCE','READ_ONLY')",
            name="role_valid",
        ),
    )


# ===========================================================================
# Taxpayer
# ===========================================================================


class Taxpayer(Base, TimestampMixin):
    __tablename__ = "taxpayers"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    middle_initial: Mapped[str] = mapped_column(String(1), default="")
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)

    ssn_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    ssn_last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    """Stored in the clear so the UI can render ***-**-1234 and support can
    confirm identity, without decrypting. Four digits alone are not the
    identifier."""

    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), default="")

    address_line1: Mapped[str] = mapped_column(String(64), default="")
    address_line2: Mapped[str] = mapped_column(String(64), default="")
    city: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(2), default="")
    zip_code: Mapped[str] = mapped_column(String(10), default="")

    is_us_citizen_or_resident: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="taxpayers")
    returns: Mapped[list[TaxReturn]] = relationship(back_populates="taxpayer")


# ===========================================================================
# Returns and versioning
# ===========================================================================


class TaxReturn(Base, TimestampMixin):
    """A taxpayer's return for one tax year -- the container for its versions."""

    __tablename__ = "tax_returns"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    taxpayer_id: Mapped[str] = mapped_column(
        ForeignKey("taxpayers.id", ondelete="RESTRICT"), nullable=False
    )
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)

    current_version_id: Mapped[str | None] = mapped_column(String(36))
    files_federal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    files_oklahoma: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    taxpayer: Mapped[Taxpayer] = relationship(back_populates="returns")
    versions: Mapped[list[TaxReturnVersion]] = relationship(
        back_populates="tax_return", order_by="TaxReturnVersion.version_number"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "tax_year", name="one_return_per_user_year"),
        Index("ix_tax_returns_user_year", "user_id", "tax_year"),
    )


class TaxReturnVersion(Base, TimestampMixin):
    """One immutable version of a return.

    Editing a finalized version is not permitted. The interview writes to a
    DRAFT version; finalizing freezes it; a correction after a rejection
    creates version N+1 and marks N SUPERSEDED. This is what makes it possible
    to answer "what exactly did we transmit in April" a year later.
    """

    __tablename__ = "tax_return_versions"

    id: Mapped[str] = uuid_pk()
    tax_return_id: Mapped[str] = mapped_column(
        ForeignKey("tax_returns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)

    # The complete engine input. Stored as a document rather than shredded
    # across twenty tables because it is written and read as a unit, and
    # because a version's input must be reconstructible byte-for-byte even
    # after the relational schema evolves.
    return_input: Mapped[dict] = mapped_column(JSONB, nullable=False)

    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("tax_return_versions.id", ondelete="RESTRICT")
    )

    tax_return: Mapped[TaxReturn] = relationship(back_populates="versions")
    calculations: Mapped[list[TaxCalculation]] = relationship(back_populates="version")

    __table_args__ = (
        UniqueConstraint("tax_return_id", "version_number", name="version_number_unique"),
        CheckConstraint(
            "status IN ('DRAFT','VALIDATED','READY_FOR_FILING','SUBMITTED',"
            "'ACCEPTED','REJECTED','SUPERSEDED')",
            name="status_valid",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
    )


class TaxCalculation(Base, TimestampMixin):
    """A stored engine run: results, trace, and the versions that produced them.

    Every run is kept, not just the latest. Recomputation happens on every
    interview change, and the sequence of snapshots is what lets support
    answer "why did my refund drop by $400 yesterday?" by diffing two rows.
    """

    __tablename__ = "tax_calculations"

    id: Mapped[str] = uuid_pk()
    version_id: Mapped[str] = mapped_column(
        ForeignKey("tax_return_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    federal_rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    oklahoma_rule_version: Mapped[str | None] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    federal_agi: Mapped[Decimal | None] = mapped_column(Money)
    federal_total_tax: Mapped[Decimal | None] = mapped_column(Money)
    federal_refund: Mapped[Decimal | None] = mapped_column(Money)
    federal_amount_owed: Mapped[Decimal | None] = mapped_column(Money)
    oklahoma_total_tax: Mapped[Decimal | None] = mapped_column(Money)
    oklahoma_refund: Mapped[Decimal | None] = mapped_column(Money)
    oklahoma_amount_owed: Mapped[Decimal | None] = mapped_column(Money)

    federal_result: Mapped[dict | None] = mapped_column(JSONB)
    oklahoma_result: Mapped[dict | None] = mapped_column(JSONB)
    trace: Mapped[dict | None] = mapped_column(JSONB)
    findings: Mapped[list | None] = mapped_column(JSONB)

    rule_sets_certified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    version: Mapped[TaxReturnVersion] = relationship(back_populates="calculations")


# ===========================================================================
# Documents
# ===========================================================================


class TaxDocument(Base, TimestampMixin):
    __tablename__ = "tax_documents"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tax_return_id: Mapped[str | None] = mapped_column(
        ForeignKey("tax_returns.id", ondelete="RESTRICT"), index=True
    )

    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Object store key, not file contents. The object itself is encrypted at
    # rest with a key the database has no access to, so a database compromise
    # does not yield the taxpayer's W-2 image.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    malware_scan_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )
    malware_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "malware_scan_status IN ('PENDING','CLEAN','INFECTED','FAILED')",
            name="scan_status_valid",
        ),
        CheckConstraint(
            "extraction_status IN ('PENDING','COMPLETE','FAILED','NOT_APPLICABLE')",
            name="extraction_status_valid",
        ),
    )


class DocumentExtraction(Base, TimestampMixin):
    """One value read off a document, with its confidence and verification state."""

    __tablename__ = "document_extractions"

    id: Mapped[str] = uuid_pk()
    document_id: Mapped[str] = mapped_column(
        ForeignKey("tax_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)

    verified_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrected_value: Mapped[str | None] = mapped_column(Text)
    """Set when the taxpayer changed what was extracted. Kept separately from
    the extracted value so extractor accuracy can be measured against real
    corrections rather than estimated."""

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )


# ===========================================================================
# Filing, payment and consent
# ===========================================================================


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tax_return_id: Mapped[str] = mapped_column(
        ForeignKey("tax_returns.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)

    # Processor reference only. No card data of any kind reaches this database,
    # which is what keeps OlbosTax out of PCI DSS scope for cardholder storage.
    processor_name: Mapped[str] = mapped_column(String(32), nullable=False)
    processor_reference: Mapped[str | None] = mapped_column(String(128), index=True)
    card_last_four: Mapped[str | None] = mapped_column(String(4))
    card_brand: Mapped[str | None] = mapped_column(String(16))

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PAID','FAILED','REFUNDED')", name="status_valid"
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )


class Consent(Base, TimestampMixin):
    """A recorded taxpayer authorization.

    Consents are append-only evidence. A taxpayer's authorization to file, and
    their consent to the terms under which it happened, may be needed years
    later, so these rows are never updated -- withdrawing consent records a new
    row rather than modifying the old one.
    """

    __tablename__ = "consents"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tax_return_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("tax_return_versions.id", ondelete="RESTRICT")
    )

    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_version: Mapped[str] = mapped_column(String(32), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    """Hash of the exact text shown. Without it, "they agreed to the terms" is
    an assertion about a document that has since changed."""

    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))


class Signature(Base, TimestampMixin):
    """The taxpayer's electronic signature on a specific return version."""

    __tablename__ = "signatures"

    id: Mapped[str] = uuid_pk()
    tax_return_version_id: Mapped[str] = mapped_column(
        ForeignKey("tax_return_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    signature_method: Mapped[str] = mapped_column(String(32), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))

    # Binds the signature to exact content. If the return changes, the
    # signature no longer matches, which is the whole point of signing.
    signed_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    spouse_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    self_select_pin_last_four: Mapped[str | None] = mapped_column(String(4))


class EFileSubmission(Base, TimestampMixin):
    __tablename__ = "efile_submissions"

    id: Mapped[str] = uuid_pk()
    tax_return_version_id: Mapped[str] = mapped_column(
        ForeignKey("tax_return_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    jurisdiction: Mapped[str] = mapped_column(String(8), nullable=False)

    submission_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(128))

    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    """MOCK, TEST or LIVE. Stored so that no query, report or support screen
    can present a simulated submission as a real filing."""

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"))
    signature_id: Mapped[str | None] = mapped_column(ForeignKey("signatures.id"))

    __table_args__ = (
        CheckConstraint("channel IN ('MOCK','TEST','LIVE')", name="channel_valid"),
        CheckConstraint("jurisdiction IN ('FEDERAL','OK')", name="jurisdiction_valid"),
        UniqueConstraint("submission_id", "jurisdiction", name="submission_unique"),
    )


class EFileAcknowledgment(Base, TimestampMixin):
    __tablename__ = "efile_acknowledgments"

    id: Mapped[str] = uuid_pk()
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("efile_submissions.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    authority_reference: Mapped[str | None] = mapped_column(String(128))
    rejection_codes: Mapped[list | None] = mapped_column(JSONB)
    rejection_issues: Mapped[list | None] = mapped_column(JSONB)
    raw_payload_reference: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        CheckConstraint("channel IN ('MOCK','TEST','LIVE')", name="channel_valid"),
        # The database-level restatement of the e-file layer's core invariant.
        # Belt and braces: the Python model already refuses to construct such
        # an object, and this makes it unrepresentable in storage even if a
        # future code path writes rows directly.
        CheckConstraint(
            "status <> 'ACCEPTED' OR (channel = 'LIVE' AND authority_reference IS NOT NULL)",
            name="acceptance_requires_live_channel_and_reference",
        ),
    )


# ===========================================================================
# Audit and security
# ===========================================================================


class AuditLog(Base):
    """Append-only record of consequential events.

    No ``updated_at`` and no update path: an audit log that can be edited is
    not an audit log. The application role is granted INSERT and SELECT on this
    table and nothing else (see the migration).

    Metadata is JSONB and must never contain an SSN or an account number --
    enforced by the redaction layer before anything reaches here.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = uuid_pk()
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    admin_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tax_return_id: Mapped[str | None] = mapped_column(String(36), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))

    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    event_metadata: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_audit_logs_user_time", "user_id", "occurred_at"),
        Index("ix_audit_logs_type_time", "event_type", "occurred_at"),
    )


class SecurityEvent(Base):
    """Security-relevant occurrences, separated from the general audit log.

    Kept apart because the access patterns and retention differ: security
    events feed alerting and are queried by the security team, while audit
    logs answer "what happened to this return".
    """

    __tablename__ = "security_events"

    id: Mapped[str] = uuid_pk()
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="severity_valid"
        ),
    )


class SupportCase(Base, TimestampMixin):
    __tablename__ = "support_cases"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tax_return_id: Mapped[str | None] = mapped_column(ForeignKey("tax_returns.id"))

    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    assigned_admin_id: Mapped[str | None] = mapped_column(ForeignKey("admin_users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','WAITING_ON_CUSTOMER','ESCALATED','RESOLVED','CLOSED')",
            name="status_valid",
        ),
    )


class SensitiveDataAccess(Base):
    """Every read of unmasked taxpayer data by a staff member.

    Spec section 24: support does not see full SSNs or bank details by
    default, elevated access is required, and every such access is logged.
    This table is that log, and it is what makes the control auditable rather
    than merely stated.
    """

    __tablename__ = "sensitive_data_accesses"

    id: Mapped[str] = uuid_pk()
    admin_user_id: Mapped[str] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    field_accessed: Mapped[str] = mapped_column(String(64), nullable=False)
    justification: Mapped[str] = mapped_column(String(512), nullable=False)
    support_case_id: Mapped[str | None] = mapped_column(ForeignKey("support_cases.id"))
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64))


class RuleSetApproval(Base, TimestampMixin):
    """Human approval of a tax rule set for production use.

    Spec section 42 requires rule changes to pass tests, regression, human
    review and approval before production. This table is the approval record,
    and a rule set's certification is only meaningful if the approval that
    granted it can be produced on demand.
    """

    __tablename__ = "rule_set_approvals"

    id: Mapped[str] = uuid_pk()
    jurisdiction: Mapped[str] = mapped_column(String(16), nullable=False)
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    """Hash of the rule file as approved. A rule set edited after approval no
    longer matches, and the certification no longer applies to it."""

    certification: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_by_admin_id: Mapped[str] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer_notes: Mapped[str] = mapped_column(Text, default="")
    regression_suite_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "jurisdiction", "tax_year", "rule_version", name="rule_version_unique"
        ),
        CheckConstraint(
            "certification IN ('DRAFT','UNDER_REVIEW','PRODUCTION')", name="certification_valid"
        ),
    )
