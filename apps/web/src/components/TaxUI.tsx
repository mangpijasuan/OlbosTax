"use client";

import { useId, useState } from "react";
import { money } from "@/lib/format";
import type { CalculationBreakdownStep, CapabilityFinding } from "@/lib/api";

/* ==========================================================================
   Reusable interview components (spec section 29).

   The design constraint running through all of them: a taxpayer filling these
   in is not a data-entry clerk. Labels say what the question means, not what
   the form calls it, and the form line reference is available for the people
   who want it without being in the way of the people who do not.
   ========================================================================== */

export function MoneyInput({
  label,
  hint,
  value,
  onChange,
  error,
  formLine,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  formLine?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {hint && (
        <span className="field__hint" id={hintId}>
          {hint}
          {formLine && <> Look for {formLine}.</>}
        </span>
      )}
      <div className="input-group">
        <span className="input-group__prefix" aria-hidden="true">
          $
        </span>
        <input
          id={id}
          className="input input--money"
          type="text"
          inputMode="decimal"
          /* inputMode="decimal" rather than type="number": a number input on
             mobile hides the decimal point on some keyboards, silently rejects
             a pasted "1,234.56", and lets a scroll wheel change an amount the
             taxpayer already entered. */
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-describedby={[hint ? hintId : null, error ? errorId : null]
            .filter(Boolean)
            .join(" ") || undefined}
          aria-invalid={error ? "true" : undefined}
          autoComplete="off"
        />
      </div>
      {error && (
        <span className="field__error" id={errorId} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export function ProgressStepper({
  steps,
  currentIndex,
}: {
  steps: string[];
  currentIndex: number;
}) {
  return (
    <nav aria-label="Your progress">
      <ol className="stepper">
        {steps.map((step, index) => {
          const state =
            index < currentIndex ? "done" : index === currentIndex ? "current" : "todo";
          return (
            <li key={step} className="stepper__step" data-state={state}>
              <span className="visually-hidden">
                {state === "done"
                  ? "Completed: "
                  : state === "current"
                    ? "Current step: "
                    : "Not started: "}
              </span>
              {step}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function RefundSummary({
  federalRefund,
  federalOwed,
  oklahomaRefund,
  oklahomaOwed,
  provisional,
}: {
  federalRefund: string | null;
  federalOwed: string | null;
  oklahomaRefund: string | null;
  oklahomaOwed: string | null;
  provisional: boolean;
}) {
  const fedNet = Number(federalRefund ?? 0) - Number(federalOwed ?? 0);
  const okNet = Number(oklahomaRefund ?? 0) - Number(oklahomaOwed ?? 0);
  const total = fedNet + okNet;

  return (
    <div className="card" aria-live="polite">
      {provisional && (
        <p
          className="small"
          style={{
            color: "var(--warn)",
            fontWeight: 600,
            margin: "0 0 0.75rem",
            display: "flex",
            gap: "0.4rem",
          }}
        >
          <span aria-hidden="true">⏳</span>
          Estimate only — this year&apos;s tax rules are still being verified
        </p>
      )}

      <div className="small muted">{total >= 0 ? "Estimated refund" : "Estimated amount you owe"}</div>
      <div
        className={`money money--xl ${total >= 0 ? "money--refund" : "money--owed"}`}
      >
        {money(Math.abs(total))}
      </div>

      <dl
        style={{
          display: "grid",
          gap: "0.5rem",
          margin: "1.25rem 0 0",
          paddingTop: "1rem",
          borderTop: "1px solid var(--ink-100)",
        }}
      >
        <Row label="Federal" net={fedNet} />
        <Row label="Oklahoma" net={okNet} />
      </dl>
    </div>
  );
}

function Row({ label, net }: { label: string; net: number }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
      <dt className="muted">{label}</dt>
      <dd
        className={`money ${net >= 0 ? "money--refund" : "money--owed"}`}
        style={{ margin: 0 }}
      >
        {net >= 0 ? money(net) : `${money(Math.abs(net))} owed`}
      </dd>
    </div>
  );
}

/**
 * The "How was this calculated?" disclosure (spec section 12).
 *
 * Collapsed by default. A taxpayer who trusts the number should not have to
 * scroll past twenty lines of arithmetic to reach the next question, and one
 * who does not trust it needs every step. Progressive disclosure serves both;
 * showing it always serves neither.
 */
export function CalculationBreakdown({
  steps,
  title = "How was this calculated?",
}: {
  steps: CalculationBreakdownStep[];
  title?: string;
}) {
  const [open, setOpen] = useState(false);

  if (steps.length === 0) return null;

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <button
        type="button"
        className="btn btn--ghost"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        style={{ paddingInline: 0, color: "var(--teal-700)", fontWeight: 640 }}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span> {title}
      </button>

      {open && (
        <div className="card" style={{ marginTop: "0.75rem", padding: "1.25rem" }}>
          <dl style={{ margin: 0, display: "grid", gap: "1rem" }}>
            {steps.map((step) => (
              <div key={step.code + step.form_line}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "1rem",
                    alignItems: "baseline",
                  }}
                >
                  <dt style={{ fontWeight: 600 }}>{step.label}</dt>
                  <dd className="money" style={{ margin: 0, whiteSpace: "nowrap" }}>
                    {money(step.amount)}
                  </dd>
                </div>
                {step.detail && (
                  <p className="small muted" style={{ margin: "0.2rem 0 0" }}>
                    {step.detail}
                  </p>
                )}
                {step.form_line && (
                  <p className="small muted" style={{ margin: "0.2rem 0 0", fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
                    {step.form_line}
                    {step.rule_citation ? ` · ${step.rule_citation}` : ""}
                  </p>
                )}
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

/**
 * Capability findings, rendered as the error centre of spec section 16.
 *
 * Every finding shows what happened, why it matters, and what to do about it.
 * None of them show a code, a field path, or a schema message: those go to
 * support tooling, not to the person trying to file.
 */
export function FindingList({ findings }: { findings: CapabilityFinding[] }) {
  if (findings.length === 0) return null;

  const blocking = findings.filter(
    (f) => f.level === "NOT_SUPPORTED" || f.level === "REQUIRES_TAX_PROFESSIONAL",
  );
  const advisory = findings.filter((f) => !blocking.includes(f));

  return (
    <section aria-label="Things that need your attention" style={{ marginTop: "1.5rem" }}>
      {blocking.map((finding) => (
        <Finding key={finding.code} finding={finding} tone="danger" icon="⚠" />
      ))}
      {advisory.map((finding) => (
        <Finding key={finding.code} finding={finding} tone="warn" icon="ℹ" />
      ))}
    </section>
  );
}

function Finding({
  finding,
  tone,
  icon,
}: {
  finding: CapabilityFinding;
  tone: "danger" | "warn";
  icon: string;
}) {
  return (
    <div className={`notice notice--${tone}`}>
      <span className="notice__icon" aria-hidden="true">
        {icon}
      </span>
      <div className="notice__body">
        <p className="notice__title">{finding.title}</p>
        <p className="small">{finding.explanation}</p>
        {finding.guidance && (
          <p className="small" style={{ fontWeight: 560 }}>
            {finding.guidance}
          </p>
        )}
      </div>
    </div>
  );
}
