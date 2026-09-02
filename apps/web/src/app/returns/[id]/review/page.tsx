"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { CalculationBreakdown, FindingList, RefundSummary } from "@/components/TaxUI";
import { api, hasAccessToken, type Computation } from "@/lib/api";
import { money } from "@/lib/format";

/**
 * Statuses whose return version the API will refuse to modify.
 *
 * Kept in step with `ReturnStatus.is_finalized` by
 * tests/api/test_cross_language_consistency.py -- the API and the UI acting on
 * different lists is a silent failure that surfaces as an error message shown
 * to a taxpayer looking at a return they already filed.
 */
const FINALIZED_STATUSES = new Set([
  "SUBMITTED",
  "ACCEPTED",
  "REJECTED",
  "SUPERSEDED",
]);

/**
 * Final review (spec sections 12, 16 and 31).
 *
 * The last screen before payment, and the one where the promise of
 * transparency is either kept or broken. It shows the bottom line, everything
 * blocking the return, the arithmetic behind the figures, and -- when filing
 * is not available -- says so plainly instead of routing the taxpayer to a
 * checkout that will fail.
 */
export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [computation, setComputation] = useState<Computation | null>(null);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasAccessToken()) {
      setError("Please sign in to see this return.");
      return;
    }
    api
      .getReturn(id)
      .then(async (details) => {
        setStatus(details.status);

        // A finalized return is read-only. Saving it to force a fresh
        // calculation would be rejected with a 409, and the taxpayer would be
        // shown an error for the ordinary act of looking at a return they have
        // already filed. Read the stored calculation instead -- which is also
        // the more correct thing to display: what was computed at the time it
        // was filed, not what today's engine makes of the same inputs.
        if (FINALIZED_STATUSES.has(details.status)) {
          setComputation(await api.getStoredComputation(id));
          return;
        }

        const saved = await api.saveReturn(id, details.return_input);
        setComputation(saved.computation);
      })
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : "We could not load this return."),
      );
  }, [id]);

  if (error) {
    return (
      <Shell>
        <div className="notice notice--danger" role="alert">
          <span className="notice__icon" aria-hidden="true">⚠</span>
          <div className="notice__body">
            <p className="notice__title">We could not open this return</p>
            <p className="small">{error}</p>
          </div>
        </div>
        <Link className="btn btn--secondary" href="/dashboard">Back to my returns</Link>
      </Shell>
    );
  }

  if (!computation) {
    return (
      <Shell>
        <p className="muted" role="status">Working out your return…</p>
      </Shell>
    );
  }

  const federal = computation.federal;
  const oklahoma = computation.oklahoma;
  const blocking = computation.findings.filter(
    (f) => f.level === "NOT_SUPPORTED" || f.level === "REQUIRES_TAX_PROFESSIONAL",
  );

  return (
    <Shell>
      <h1>Review your {status === "DRAFT" ? "return" : "filed return"}</h1>
      <p className="muted">
        Check everything below before you file. You are the one signing this
        return.
      </p>

      <RefundSummary
        federalRefund={federal?.refund ?? null}
        federalOwed={federal?.amount_owed ?? null}
        oklahomaRefund={oklahoma?.refund ?? null}
        oklahomaOwed={oklahoma?.amount_owed ?? null}
        provisional={!computation.rule_sets_certified}
      />

      <FindingList findings={computation.findings} />

      {federal && (
        <section style={{ marginTop: "2.5rem" }}>
          <h2 style={{ fontSize: "1.25rem" }}>Your federal return</h2>
          <SummaryTable
            rows={[
              ["Adjusted gross income", federal.adjusted_gross_income],
              [
                federal.deduction_is_itemized ? "Itemised deductions" : "Standard deduction",
                federal.deduction_taken,
              ],
              ["Taxable income", federal.taxable_income],
              ["Total tax", federal.total_tax],
            ]}
          />
          <FormList forms={federal.required_forms} />
        </section>
      )}

      {oklahoma && (
        <section style={{ marginTop: "2.5rem" }}>
          <h2 style={{ fontSize: "1.25rem" }}>Your Oklahoma return</h2>
          <SummaryTable
            rows={[
              ["Oklahoma adjusted gross income", oklahoma.oklahoma_adjusted_gross_income],
              ["Oklahoma taxable income", oklahoma.oklahoma_taxable_income],
              ["Total Oklahoma tax", oklahoma.total_tax],
            ]}
          />
          <FormList forms={oklahoma.required_forms} />
        </section>
      )}

      <CalculationBreakdown steps={computation.breakdown} title="Show me every step" />

      <section style={{ marginTop: "3rem", paddingTop: "2rem", borderTop: "1px solid var(--ink-100)" }}>
        {computation.can_be_filed && blocking.length === 0 ? (
          <>
            <h2 style={{ fontSize: "1.25rem" }}>Ready to file</h2>
            <p className="small muted">
              You will be charged $14.99 once, at the point you confirm. Nothing
              is filed until you sign.
            </p>
            <Link className="btn btn--primary" href={`/returns/${id}/pay`}>
              Continue to payment — $14.99
            </Link>
          </>
        ) : (
          <>
            <h2 style={{ fontSize: "1.25rem" }}>Not ready to file</h2>
            <div className="notice notice--warn">
              <span className="notice__icon" aria-hidden="true">⏳</span>
              <div className="notice__body">
                <p className="notice__title">
                  {blocking.length > 0
                    ? "Your return includes something we cannot file"
                    : "Filing is not open for this tax year yet"}
                </p>
                <p className="small">
                  {blocking.length > 0
                    ? "See the items above. We will not file a return we cannot compute correctly."
                    : "The tax rules for this year have not finished verification against official sources. We will email you as soon as you can file. Nothing you have entered will be lost, and you have not been charged."}
                </p>
              </div>
            </div>
            <button className="btn btn--primary" type="button" aria-disabled="true" disabled>
              Continue to payment — $14.99
            </button>
          </>
        )}
      </section>
    </Shell>
  );
}

function SummaryTable({ rows }: { rows: [string, string][] }) {
  return (
    <dl style={{ margin: "1rem 0 0", display: "grid", gap: "0.55rem" }}>
      {rows.map(([label, value]) => (
        <div
          key={label}
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: "1rem",
            paddingBottom: "0.55rem",
            borderBottom: "1px solid var(--ink-050)",
          }}
        >
          <dt>{label}</dt>
          <dd className="money" style={{ margin: 0 }}>{money(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function FormList({ forms }: { forms: string[] }) {
  if (forms.length === 0) return null;
  return (
    <p className="small muted" style={{ marginTop: "0.75rem" }}>
      Forms included: {forms.join(", ")}. We only prepare the forms your
      situation actually needs.
    </p>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader signedIn />
      <main id="main" className="shell" style={{ paddingBlock: "2.5rem 1rem", maxWidth: "44rem" }}>
        {children}
      </main>
      <SiteFooter />
    </>
  );
}
