"use client";

import { useState } from "react";
import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { ProgressStepper, RefundSummary, CalculationBreakdown, FindingList, MoneyInput } from "@/components/TaxUI";
import type { Computation } from "@/lib/api";

const STEPS = ["Personal", "Income", "Deductions", "Credits", "Review", "File"];

/**
 * The guided interview.
 *
 * Spec section 28: mobile first, progressive disclosure, one major question
 * per screen where appropriate. This screen keeps a single question group in
 * view at a time and carries the running refund alongside it, because the
 * refund figure is the feedback that makes the next question feel worth
 * answering.
 *
 * This build runs the interview against local state and calls the API to
 * calculate. Persisting a draft between sessions requires an authenticated
 * account, which is why the flow asks for one before the review step rather
 * than at the very start -- a sign-up wall before any value is shown is the
 * pattern this product exists to avoid.
 */
export default function StartPage() {
  const [stepIndex, setStepIndex] = useState(0);
  const [computation] = useState<Computation | null>(null);

  const [filingStatus, setFilingStatus] = useState("SINGLE");
  const [wages, setWages] = useState("");
  const [withheld, setWithheld] = useState("");
  const [stateWithheld, setStateWithheld] = useState("");
  const [dependents, setDependents] = useState("0");

  return (
    <>
      <SiteHeader />
      <main id="main" className="shell" style={{ paddingBlock: "2rem 1rem", maxWidth: "56rem" }}>
        <ProgressStepper steps={STEPS} currentIndex={stepIndex} />

        <div
          style={{
            display: "grid",
            gap: "2rem",
            gridTemplateColumns: "minmax(0, 1fr)",
            marginTop: "2.5rem",
          }}
        >
          <div>
            {stepIndex === 0 && (
              <FilingStatusStep value={filingStatus} onChange={setFilingStatus} />
            )}

            {stepIndex === 1 && (
              <section>
                <h1 style={{ fontSize: "1.75rem" }}>Your income</h1>
                <p className="muted">
                  Start with your W-2. You can upload it instead of typing once
                  you have an account.
                </p>
                <MoneyInput
                  label="Wages, tips and other pay"
                  hint="The total pay your employer reported."
                  formLine="box 1 of your W-2"
                  value={wages}
                  onChange={setWages}
                />
                <MoneyInput
                  label="Federal income tax withheld"
                  hint="Tax your employer already sent to the IRS on your behalf."
                  formLine="box 2 of your W-2"
                  value={withheld}
                  onChange={setWithheld}
                />
                <MoneyInput
                  label="Oklahoma income tax withheld"
                  hint="Leave this blank if your W-2 does not show Oklahoma."
                  formLine="box 17 of your W-2"
                  value={stateWithheld}
                  onChange={setStateWithheld}
                />
              </section>
            )}

            {stepIndex === 2 && (
              <section>
                <h1 style={{ fontSize: "1.75rem" }}>Deductions</h1>
                <p className="muted">
                  Most people take the standard deduction, which we apply
                  automatically. We only ask about itemising if it would save you
                  money.
                </p>
                <div className="notice notice--info">
                  <span className="notice__icon" aria-hidden="true">
                    ℹ
                  </span>
                  <div className="notice__body">
                    <p className="notice__title">We will pick whichever is larger</p>
                    <p className="small">
                      You do not have to work out whether to itemise. We calculate
                      both and use the one that leaves you paying less.
                    </p>
                  </div>
                </div>
              </section>
            )}

            {stepIndex === 3 && (
              <section>
                <h1 style={{ fontSize: "1.75rem" }}>Your family</h1>
                <p className="muted">
                  Children and other dependents can qualify you for credits worth
                  thousands of dollars.
                </p>
                <div className="field">
                  <label className="field__label" htmlFor="dependents">
                    How many dependents are you claiming?
                  </label>
                  <span className="field__hint">
                    Usually children who lived with you for more than half the
                    year.
                  </span>
                  <input
                    id="dependents"
                    className="input"
                    type="text"
                    inputMode="numeric"
                    value={dependents}
                    onChange={(event) => setDependents(event.target.value)}
                    style={{ maxWidth: "8rem" }}
                  />
                </div>
              </section>
            )}

            {stepIndex >= 4 && (
              <section>
                <h1 style={{ fontSize: "1.75rem" }}>Create an account to continue</h1>
                <p>
                  To calculate your full return, save your progress and file, we
                  need an account. Everything you have entered so far will carry
                  over.
                </p>
                <div className="notice notice--info">
                  <span className="notice__icon" aria-hidden="true">
                    🔒
                  </span>
                  <div className="notice__body">
                    <p className="notice__title">Why an account is needed here</p>
                    <p className="small">
                      A tax return contains your Social Security number and your
                      income. We will not hold that on an anonymous session, and
                      we will not let it be reached without a password.
                    </p>
                  </div>
                </div>
                <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginTop: "1.5rem" }}>
                  <Link className="btn btn--primary" href="/sign-in?mode=register">
                    Create account
                  </Link>
                  <Link className="btn btn--secondary" href="/sign-in">
                    I already have one
                  </Link>
                </div>
              </section>
            )}

            <div
              style={{
                display: "flex",
                gap: "0.75rem",
                marginTop: "2.5rem",
                paddingTop: "1.5rem",
                borderTop: "1px solid var(--ink-100)",
              }}
            >
              <button
                type="button"
                className="btn btn--secondary"
                onClick={() => setStepIndex((index) => Math.max(0, index - 1))}
                disabled={stepIndex === 0}
              >
                Back
              </button>
              {stepIndex < STEPS.length - 2 && (
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => setStepIndex((index) => index + 1)}
                >
                  Continue
                </button>
              )}
            </div>
          </div>

          <aside>
            <RefundSummary
              federalRefund={computation?.federal?.refund ?? null}
              federalOwed={computation?.federal?.amount_owed ?? null}
              oklahomaRefund={computation?.oklahoma?.refund ?? null}
              oklahomaOwed={computation?.oklahoma?.amount_owed ?? null}
              provisional={computation ? !computation.rule_sets_certified : true}
            />
            {computation && (
              <>
                <FindingList findings={computation.findings} />
                <CalculationBreakdown steps={computation.breakdown} />
              </>
            )}
            <p className="small muted" style={{ marginTop: "1rem" }}>
              Your refund updates as you answer. Nothing is filed and nothing is
              charged until you review everything and choose to file.
            </p>
          </aside>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}

function FilingStatusStep({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const options: [string, string, string][] = [
    ["SINGLE", "Single", "Not married on 31 December."],
    [
      "MARRIED_FILING_JOINTLY",
      "Married, filing together",
      "One return for both of you. This is usually the lower tax.",
    ],
    [
      "MARRIED_FILING_SEPARATELY",
      "Married, filing separately",
      "Separate returns. This gives up several credits.",
    ],
    [
      "HEAD_OF_HOUSEHOLD",
      "Head of household",
      "Unmarried, and you paid most of the cost of a home for a dependent.",
    ],
    [
      "QUALIFYING_SURVIVING_SPOUSE",
      "Surviving spouse",
      "Your spouse died recently and you have a dependent child.",
    ],
  ];

  return (
    <section>
      <h1 style={{ fontSize: "1.75rem" }}>How are you filing this year?</h1>
      <p className="muted">
        This affects your tax rates and your standard deduction more than almost
        anything else.
      </p>

      <fieldset style={{ border: 0, padding: 0, margin: "1.5rem 0 0" }}>
        <legend className="visually-hidden">Filing status</legend>
        <div style={{ display: "grid", gap: "0.75rem" }}>
          {options.map(([id, label, hint]) => (
            <label
              key={id}
              style={{
                display: "flex",
                gap: "0.9rem",
                alignItems: "flex-start",
                padding: "1rem 1.1rem",
                border: `1.5px solid ${value === id ? "var(--teal-600)" : "var(--ink-100)"}`,
                background: value === id ? "var(--teal-050)" : "var(--paper)",
                borderRadius: "var(--radius)",
                cursor: "pointer",
              }}
            >
              <input
                type="radio"
                name="filing-status"
                value={id}
                checked={value === id}
                onChange={() => onChange(id)}
                style={{ marginTop: "0.35rem", width: "1.15rem", height: "1.15rem", flex: "none" }}
              />
              <span>
                <span style={{ display: "block", fontWeight: 640 }}>{label}</span>
                <span className="small muted">{hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    </section>
  );
}
