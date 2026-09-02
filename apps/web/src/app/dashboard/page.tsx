"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { api, hasAccessToken, type ReturnSummary } from "@/lib/api";
import { money } from "@/lib/format";

/**
 * The signed-in dashboard (spec section 30).
 *
 * Shows the current year's return with its progress and refund, and the
 * history beneath it. A taxpayer's first question on arriving here is "where
 * did I get to and how much am I getting back", so those are the two things
 * above the fold.
 */
export default function DashboardPage() {
  const [returns, setReturns] = useState<ReturnSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasAccessToken()) {
      setError("signed-out");
      return;
    }
    api
      .listReturns()
      .then(setReturns)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "unknown"));
  }, []);

  if (error === "signed-out") {
    return (
      <Shell>
        <div className="card" style={{ maxWidth: "30rem" }}>
          <h1 style={{ fontSize: "1.4rem" }}>Please sign in</h1>
          <p className="muted small">
            Your session ended. For a product holding tax data we keep sessions
            short on purpose.
          </p>
          <Link className="btn btn--primary" href="/sign-in">
            Sign in
          </Link>
        </div>
      </Shell>
    );
  }

  if (error) {
    return (
      <Shell>
        <div className="notice notice--danger">
          <span className="notice__icon" aria-hidden="true">
            ⚠
          </span>
          <div className="notice__body">
            <p className="notice__title">We could not load your returns</p>
            <p className="small">{error}</p>
          </div>
        </div>
      </Shell>
    );
  }

  if (returns === null) {
    return (
      <Shell>
        <p className="muted" role="status">
          Loading your returns…
        </p>
      </Shell>
    );
  }

  const [current, ...previous] = returns;

  return (
    <Shell>
      <h1>Welcome back</h1>

      {current ? <CurrentReturnCard summary={current} /> : <NoReturnsCard />}

      {previous.length > 0 && (
        <section style={{ marginTop: "3rem" }}>
          <h2 style={{ fontSize: "1.25rem" }}>Past returns</h2>
          <ul style={{ listStyle: "none", padding: 0, margin: "1rem 0 0", display: "grid", gap: "0.6rem" }}>
            {previous.map((summary) => (
              <li
                key={summary.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "1rem",
                  padding: "1rem 1.15rem",
                  border: "1px solid var(--ink-100)",
                  borderRadius: "var(--radius)",
                  background: "var(--paper)",
                }}
              >
                <div>
                  <div style={{ fontWeight: 640 }}>{summary.tax_year}</div>
                  <div className="small muted">{statusLabel(summary.status)}</div>
                </div>
                <Link className="btn btn--ghost" href={`/returns/${summary.id}/review`}>
                  View
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Shell>
  );
}

function CurrentReturnCard({ summary }: { summary: ReturnSummary }) {
  const federal = Number(summary.federal_refund ?? 0) - Number(summary.federal_amount_owed ?? 0);
  const oklahoma = Number(summary.oklahoma_refund ?? 0) - Number(summary.oklahoma_amount_owed ?? 0);
  const total = federal + oklahoma;
  const progress = progressFor(summary.status);

  return (
    <div className="card" style={{ marginTop: "1.5rem", maxWidth: "34rem" }}>
      <div className="small muted">{summary.tax_year} tax return</div>
      <div style={{ fontWeight: 640, fontSize: "1.15rem", marginBottom: "1rem" }}>
        {statusLabel(summary.status)}
      </div>

      <div
        role="progressbar"
        aria-valuenow={progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Return progress"
        style={{
          height: "10px",
          borderRadius: "999px",
          background: "var(--ink-100)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${progress}%`,
            height: "100%",
            background: "var(--teal-600)",
          }}
        />
      </div>
      <div className="small muted" style={{ marginTop: "0.4rem" }}>
        {progress}% complete
      </div>

      <dl style={{ display: "grid", gap: "0.5rem", margin: "1.5rem 0 0" }}>
        <Line label="Federal" value={federal} />
        <Line label="Oklahoma" value={oklahoma} />
      </dl>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginTop: "1rem",
          paddingTop: "1rem",
          borderTop: "1px solid var(--ink-100)",
        }}
      >
        <span style={{ fontWeight: 640 }}>
          {total >= 0 ? "Estimated total refund" : "Estimated total owed"}
        </span>
        <span className={`money money--lg ${total >= 0 ? "money--refund" : "money--owed"}`}>
          {money(Math.abs(total))}
        </span>
      </div>

      {!summary.can_be_filed && (
        <p className="small" style={{ color: "var(--warn)", marginTop: "0.75rem" }}>
          Estimate only — this return cannot be filed yet.
        </p>
      )}

      <Link
        className="btn btn--primary btn--block"
        href={`/returns/${summary.id}/review`}
        style={{ marginTop: "1.5rem" }}
      >
        Continue Return
      </Link>
    </div>
  );
}

function NoReturnsCard() {
  return (
    <div className="card" style={{ marginTop: "1.5rem", maxWidth: "30rem" }}>
      <h2 style={{ fontSize: "1.2rem" }}>You have not started a return yet</h2>
      <p className="small muted">
        It takes about twenty minutes for a straightforward return.
      </p>
      <Link className="btn btn--primary" href="/start">
        Start your return
      </Link>
    </div>
  );
}

function Line({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
      <dt className="muted">{label}</dt>
      <dd className={`money ${value >= 0 ? "money--refund" : "money--owed"}`} style={{ margin: 0 }}>
        {value >= 0 ? money(value) : `${money(Math.abs(value))} owed`}
      </dd>
    </div>
  );
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    DRAFT: "In progress",
    VALIDATED: "Checked, ready to review",
    READY_FOR_FILING: "Ready to file",
    SUBMITTED: "Submitted — waiting for a response",
    ACCEPTED: "Accepted",
    REJECTED: "Rejected — needs your attention",
    SUPERSEDED: "Replaced by a newer version",
  };
  return labels[status] ?? status;
}

function progressFor(status: string): number {
  const progress: Record<string, number> = {
    DRAFT: 40,
    VALIDATED: 70,
    READY_FOR_FILING: 85,
    SUBMITTED: 95,
    ACCEPTED: 100,
    REJECTED: 85,
    SUPERSEDED: 100,
  };
  return progress[status] ?? 10;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader signedIn />
      <main id="main" className="shell" style={{ paddingBlock: "2.5rem 1rem", maxWidth: "48rem" }}>
        {children}
      </main>
      <SiteFooter />
    </>
  );
}
