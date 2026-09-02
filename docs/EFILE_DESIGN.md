# E-file integration design

## The rule this design enforces

> **A simulated submission is never represented as a real one.**

Everything below follows from that. The failure it prevents — telling a
taxpayer their return was accepted when nothing was filed, so they discover in
November that they never filed at all — is exactly the kind a reasonable-looking
refactor introduces. So it is enforced in constructors and check constraints,
not in a review checklist.

## Where OlbosTax sits

```
  OlbosTax UI
      ↓
  OlbosTax tax engine            deterministic, versioned rules
      ↓
  Validation                     levels 1-3 implemented; 4-5 are not
      ↓
  Canonical return               TaxReturnInput + TaxComputation
      ↓
  EFileProvider                  ◀── the boundary
      ↓
  Authorized provider            NOT IMPLEMENTED, NOT AUTHORIZED
      ↓
  IRS / Oklahoma Tax Commission
```

Olbos Technologies, LLC holds no EFIN and no transmitter relationship. There is
deliberately **no code in this repository that can reach a taxing authority**.

## The interface

```python
class EFileProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def channel(self) -> TransmissionChannel: ...

    @abc.abstractmethod
    def validate_return(self, tax_return, computation, jurisdiction): ...

    @abc.abstractmethod
    def submit_return(self, tax_return, computation, jurisdiction, *,
                      signature_reference, payment_reference): ...

    @abc.abstractmethod
    def get_status(self, submission_id): ...

    @abc.abstractmethod
    def retrieve_acknowledgment(self, submission_id): ...
```

`channel` is part of the interface, not an implementation detail. Providers
must **not** be interchangeable in a way that hides which is in use, so callers
can check and the channel is recorded on every receipt, acknowledgment and
database row.

| Channel | Meaning |
| --- | --- |
| `MOCK` | Nothing left this system. |
| `TEST` | Sent to a provider's test environment. Real protocol, no filing. |
| `LIVE` | Transmitted for actual filing. |

## The four invariants

**1. Only a live channel may report acceptance.**

```python
if self.status is SubmissionStatus.ACCEPTED and not self.channel.is_real_filing:
    raise ValueError(...)
```

Enforced in the `Acknowledgment` constructor. No fixture, demo script, test
helper or mis-argued refactor can produce an object asserting the IRS accepted
a return when it did not.

**2. An accepted acknowledgment must carry the authority's reference.**
Without it there is no evidence a filing occurred.

**3. A receipt cannot carry a terminal status.** Handing a return to a
transmitter is not an outcome; acceptance arrives minutes to days later.

**4. The same rule holds in the database.**

```sql
CHECK (status <> 'ACCEPTED' OR (channel = 'LIVE' AND authority_reference IS NOT NULL))
```

Belt and braces: the model already refuses, and this makes the state
unrepresentable in storage even for a future code path that writes rows
directly. Verified against a real database — a `MOCK` acceptance and a `LIVE`
acceptance without a reference are both rejected.

## Preflight

Before any provider-specific work:

```python
def preflight(self, computation):
    if not computation.rule_sets_certified_for_filing:
        raise NotAuthorizedToTransmit(...)
    if computation.blocking_findings:
        raise NotAuthorizedToTransmit(...)
```

Two conditions stop a return: rules that have not been verified, and a
situation the engine knows it could not compute correctly.

## The mock provider is deliberately unhelpful

`MockEFileProvider` can never return an `ACCEPTED` acknowledgment, because the
model forbids it. Its `ACCEPT` scenario returns a `SUBMITTED` acknowledgment,
which is the honest answer: the return was handed over and nothing accepted it,
because nothing real is on the other end.

For UI development there is `simulate_acceptance`, named to be conspicuous in a
diff. It still returns `channel=MOCK` and status `SUBMITTED`. The restriction
is the point, not an obstacle to route around.

Its rejection scenarios model the rejections that dominate real e-file traffic
— name/SSN mismatch against SSA records, a dependent already claimed, a wrong
prior-year AGI — so the error-resolution UI is built against realistic cases.

## The factory has nothing to select

```python
def get_provider(name=None):
    if requested == "mock":
        return MockEFileProvider()
    raise NotAuthorizedToTransmit(
        f"e-file provider {requested!r} is not available in this build..."
    )
```

Asking for anything else fails loudly rather than falling back to mock. A
silent fallback would mean a deployment believing it was filing while it was
not.

Adding a provider is not a matter of writing an adapter. It is gated on the
registrations in `COMPLIANCE_STATUS.md`, and the adapter should be written only
once those are complete — so a half-finished integration cannot be enabled by
setting an environment variable.

`assert_transmission_allowed` additionally refuses a `LIVE` provider outside
production, so a staging deployment cannot file real returns from test data.

## Validation

| Level | Scope | Status |
| --- | --- | --- |
| 1 | Input validity: identifier structure, dates, duplicates, refund destination | Implemented |
| 2 | Tax validity: filing status consistency, dependent eligibility, unverified extractions | Implemented |
| 3 | Cross-form reconciliation: transposed W-2 boxes, FICA consistency, state wage divergence | Implemented |
| 4 | Federal MeF schema and business rules | **Not implemented** |
| 5 | Oklahoma e-file requirements | **Not implemented** |

Levels 4 and 5 need the current published schemas, which this repository does
not have. Writing plausible-looking rules from memory would produce software
that passes its own validation and is rejected by the authority — and worse,
would hide genuine problems behind a green checkmark.

`unimplemented_validation_levels()` declares the gap in code, surfaces it
through `/api/v1/efile/validation-coverage`, and the public "how it works" page
shows it to prospective customers.

Every issue carries a `taxpayer_message` separate from `technical_detail`,
because a rule that says
`/Return/ReturnData/IRS1040/DependentDetail[2]/DependentSSN failed pattern` is
not something a person can act on.

## Submission preconditions

Checked server-side, in order. Nothing depends on what the client believes.

1. The return version is `READY_FOR_FILING`.
2. A `PAID` payment exists for the return.
3. A signature exists for this version.
4. **The signature still matches the return content.** The signature is bound
   to a hash of the exact return; if the content changed after signing, the
   taxpayer authorized something other than what would be sent.
5. MFA is satisfied on the session.
6. Preflight passes.

## Rejection handling

A rejection produces a new draft version rather than editing the submitted one.
The submitted version is marked `SUPERSEDED` and retained, because what was
transmitted must stay reconstructible.

Rejection codes are translated into plain language with a resolution, per spec
section 16:

> Someone else has already claimed one of your dependents on their tax return.
>
> Check that the Social Security number and date of birth for each dependent
> are exactly right. If they are correct and you are entitled to claim this
> dependent, you will need to file on paper so the IRS can review both returns.

## What has to happen before live transmission

See `COMPLIANCE_STATUS.md` section "What must be true before the first real
return is filed". The short version: EFIN, suitability, MeF schemas, ATS
testing, Oklahoma acceptance testing, a payment processor, a KMS, a
penetration test — and only then an adapter.
