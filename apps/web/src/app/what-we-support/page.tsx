import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

const LEVEL_ORDER = [
  "SUPPORTED",
  "PARTIALLY_SUPPORTED",
  "NOT_SUPPORTED",
  "REQUIRES_TAX_PROFESSIONAL",
] as const;

const LEVEL_COPY: Record<string, { title: string; blurb: string; tone: string }> = {
  SUPPORTED: {
    title: "Fully supported",
    blurb: "We handle these completely.",
    tone: "var(--money)",
  },
  PARTIALLY_SUPPORTED: {
    title: "Partly supported",
    blurb:
      "We handle the common cases. Read the note before relying on us for these.",
    tone: "var(--warn)",
  },
  NOT_SUPPORTED: {
    title: "Not supported yet",
    blurb:
      "We will tell you during your return if one of these applies, and we will not file a return we cannot get right.",
    tone: "var(--owed)",
  },
  REQUIRES_TAX_PROFESSIONAL: {
    title: "Needs a tax professional",
    blurb:
      "These situations carry real risk of penalties if they are handled wrong. We would rather send you to someone qualified.",
    tone: "var(--danger)",
  },
};

/**
 * The capability matrix, published (spec section 4).
 *
 * Reachable from the landing page without an account. A taxpayer deciding
 * whether this product fits their situation should be able to find out in a
 * minute, not after an hour of data entry -- and the honest version of that
 * page is also the one that avoids refund requests and support load.
 */
export default async function WhatWeSupportPage() {
  let matrix: Record<string, { level: string; description: string }> = {};
  let unavailable = false;

  try {
    matrix = await api.capabilityMatrix();
  } catch {
    unavailable = true;
  }

  const grouped = LEVEL_ORDER.map((level) => ({
    level,
    items: Object.entries(matrix)
      .filter(([, value]) => value.level === level)
      .map(([code, value]) => ({ code, description: value.description })),
  })).filter((group) => group.items.length > 0);

  return (
    <>
      <SiteHeader />
      <main id="main" className="shell" style={{ paddingBlock: "3rem 1rem", maxWidth: "50rem" }}>
        <h1>What we can and cannot file</h1>
        <p className="muted">
          Every tax product has limits. Most of them make you find out the hard
          way. Here are ours, before you start.
        </p>

        {unavailable && (
          <div className="notice notice--warn">
            <span className="notice__icon" aria-hidden="true">
              ⚠
            </span>
            <div className="notice__body">
              <p className="notice__title">We could not load this list right now</p>
              <p className="small">
                Please try again in a moment, or contact us and we will answer
                directly.
              </p>
            </div>
          </div>
        )}

        {grouped.map(({ level, items }) => (
          <section key={level} style={{ marginTop: "2.5rem" }}>
            <h2 style={{ fontSize: "1.35rem", color: LEVEL_COPY[level].tone }}>
              {LEVEL_COPY[level].title}
            </h2>
            <p className="small muted">{LEVEL_COPY[level].blurb}</p>
            <ul style={{ listStyle: "none", padding: 0, margin: "1rem 0 0", display: "grid", gap: "0.6rem" }}>
              {items.map((item) => (
                <li
                  key={item.code}
                  style={{
                    display: "flex",
                    gap: "0.75rem",
                    padding: "0.85rem 1rem",
                    border: "1px solid var(--ink-100)",
                    borderRadius: "var(--radius)",
                    background: "var(--paper)",
                  }}
                >
                  <span aria-hidden="true" style={{ color: LEVEL_COPY[level].tone, fontWeight: 700 }}>
                    {level === "SUPPORTED" ? "✓" : level === "PARTIALLY_SUPPORTED" ? "◑" : "—"}
                  </span>
                  <span>{item.description}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}

        <div className="notice notice--info" style={{ marginTop: "3rem" }}>
          <span className="notice__icon" aria-hidden="true">
            ℹ
          </span>
          <div className="notice__body">
            <p className="notice__title">This list is enforced, not advisory</p>
            <p className="small">
              If you enter something we do not support, our software stops and
              tells you. It does not quietly produce a number and let you file it.
            </p>
          </div>
        </div>

        <p style={{ marginTop: "2rem" }}>
          <Link href="/start" className="btn btn--primary">
            Start Filing
          </Link>
        </p>
      </main>
      <SiteFooter />
    </>
  );
}
