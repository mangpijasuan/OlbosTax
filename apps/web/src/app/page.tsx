import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { FilingReadinessBanner } from "@/components/FilingReadinessBanner";

/**
 * The landing page.
 *
 * Spec section 6 asks for a polished fintech landing page with a specific
 * headline, subheadline and pricing block, and forbids claiming IRS approval,
 * government affiliation, CPA review or a guaranteed refund. Nothing on this
 * page makes a claim the product cannot currently support.
 *
 * The readiness banner near the top is the honest counterweight to the
 * marketing: OlbosTax cannot file returns yet, and a visitor learns that
 * before they invest an hour entering their W-2 rather than at checkout.
 */
export default function LandingPage() {
  return (
    <>
      <SiteHeader />
      <main id="main">
        <Hero />
        <TrustStrip />
        <Pricing />
        <HowItWorks />
        <Honesty />
        <ClosingCta />
      </main>
      <SiteFooter />
    </>
  );
}

function Hero() {
  return (
    <section className="hero">
      <div className="shell hero__grid">
        <div>
          <p
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.5rem",
              background: "var(--teal-100)",
              color: "var(--teal-700)",
              fontWeight: 640,
              fontSize: "0.9rem",
              padding: "0.35rem 0.85rem",
              borderRadius: "999px",
              marginBottom: "1.5rem",
            }}
          >
            Federal + Oklahoma
          </p>

          <h1 style={{ marginBottom: "1rem" }}>File Your Taxes for $14.99.</h1>

          <p style={{ fontSize: "1.2rem", color: "var(--ink-700)" }}>
            Federal + Oklahoma tax filing without confusing upgrades or surprise
            fees.
          </p>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.85rem",
              marginTop: "2rem",
            }}
          >
            <Link className="btn btn--primary" href="/start">
              Start Filing
            </Link>
            <Link className="btn btn--secondary" href="/how-it-works">
              See How It Works
            </Link>
          </div>

          <p className="small muted" style={{ marginTop: "1.25rem" }}>
            One price, whatever your return looks like. You only pay when you
            choose to file.
          </p>

          <FilingReadinessBanner />
        </div>

        <div className="hero__preview" aria-hidden="true">
          <RefundPreview />
        </div>
      </div>
    </section>
  );
}

/**
 * A still of the product, shown beside the headline on wide screens.
 *
 * Marked aria-hidden and built from static markup: it is illustrative, not a
 * real calculation, and a screen reader announcing invented tax figures as
 * though they were the visitor's own would be actively misleading. The figures
 * are labelled as an example on the face of the card for the same reason.
 */
function RefundPreview() {
  return (
    <div className="preview-card">
      <div className="preview-card__bar">
        <span className="preview-card__dot" />
        <span className="preview-card__dot" />
        <span className="preview-card__dot" />
        <span
          className="small muted"
          style={{ marginLeft: "0.5rem", fontWeight: 600 }}
        >
          Your 2025 return
        </span>
      </div>

      <div className="preview-card__body">
        <div className="small muted">Estimated refund</div>
        <div className="money money--xl money--refund">$2,619</div>

        <div style={{ marginTop: "1.5rem" }}>
          <div className="preview-row">
            <span className="muted">Federal refund</span>
            <span className="money money--refund">$2,183</span>
          </div>
          <div className="preview-row">
            <span className="muted">Oklahoma refund</span>
            <span className="money money--refund">$436</span>
          </div>
          <div className="preview-row">
            <span className="muted">What you pay us</span>
            <span className="money">$14.99</span>
          </div>
        </div>

        <div
          style={{
            marginTop: "1.25rem",
            paddingTop: "1rem",
            borderTop: "1px solid var(--ink-100)",
            display: "flex",
            gap: "0.5rem",
            alignItems: "flex-start",
          }}
        >
          <span style={{ color: "var(--teal-700)" }}>▸</span>
          <span className="small muted">
            How was this calculated?
          </span>
        </div>

        <p
          className="small muted"
          style={{ margin: "1rem 0 0", fontSize: "0.8rem" }}
        >
          Example figures.
        </p>
      </div>
    </div>
  );
}

function TrustStrip() {
  const items: [string, string, string][] = [
    [
      "Secure",
      "Encrypted in transit and at rest",
      "Your Social Security number and bank details are encrypted with keys the database cannot read.",
    ],
    [
      "Transparent",
      "Every number is explained",
      "Ask how any figure was calculated and see the arithmetic, line by line, against the form it lands on.",
    ],
    [
      "Guided",
      "One question at a time",
      "We ask about your year in plain language, not in tax form numbers.",
    ],
    [
      "Checked",
      "Validated before you file",
      "We check your entries against each other and tell you what needs fixing in words you can act on.",
    ],
    [
      "Tracked",
      "Filing status you can follow",
      "See exactly where your return is, and what a rejection means, in plain English.",
    ],
  ];

  return (
    <section className="shell" style={{ paddingBlock: "clamp(2.5rem, 6vw, 4rem)" }}>
      <div className="trust-grid">
        {items.map(([tag, title, body]) => (
          <div key={tag} className="card">
            <div
              className="small"
              style={{
                color: "var(--teal-700)",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: "0.5rem",
              }}
            >
              {tag}
            </div>
            <h3 style={{ fontSize: "1.05rem", marginBottom: "0.4rem" }}>{title}</h3>
            <p className="small muted" style={{ margin: 0 }}>
              {body}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Pricing() {
  return (
    <section
      id="pricing"
      style={{ background: "var(--paper-warm)", paddingBlock: "clamp(3rem, 7vw, 5rem)" }}
    >
      <div className="shell">
        <div style={{ display: "grid", gap: "2.5rem", gridTemplateColumns: "1fr", maxWidth: "62rem", marginInline: "auto" }}>
          <div className="center">
            <h2>One price. That is the whole pricing page.</h2>
            <p className="muted" style={{ marginInline: "auto" }}>
              We do not charge more because your return is complicated, and we do
              not take a share of your refund.
            </p>
          </div>

          <div
            className="card"
            style={{
              maxWidth: "27rem",
              marginInline: "auto",
              width: "100%",
              borderColor: "var(--teal-500)",
              borderWidth: "2px",
              textAlign: "center",
              padding: "2.25rem 1.75rem",
            }}
          >
            <div className="money money--xl" style={{ color: "var(--teal-700)" }}>
              $14.99
            </div>
            <div style={{ fontWeight: 640, marginTop: "0.25rem" }}>
              Federal + Oklahoma
            </div>
            <ul
              style={{
                listStyle: "none",
                padding: 0,
                margin: "1.75rem 0 0",
                display: "grid",
                gap: "0.7rem",
                textAlign: "left",
              }}
            >
              {[
                "Your federal return",
                "Your Oklahoma return",
                "Every form your situation needs",
                "Document upload and review",
                "Filing status tracking",
                "Free corrections if your return is rejected",
              ].map((item) => (
                <li key={item} style={{ display: "flex", gap: "0.65rem", alignItems: "flex-start" }}>
                  <Check />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <Link
              className="btn btn--primary btn--block"
              href="/start"
              style={{ marginTop: "1.75rem" }}
            >
              Start Filing
            </Link>
          </div>

          <div style={{ display: "grid", gap: "0.75rem", maxWidth: "34rem", marginInline: "auto" }}>
            {[
              "No surprise fees.",
              "No percentage of your refund.",
              "No upgrade prompts partway through.",
              "No charge until you choose to file.",
            ].map((line) => (
              <div key={line} style={{ display: "flex", gap: "0.65rem", alignItems: "center" }}>
                <span aria-hidden="true" style={{ color: "var(--money)", fontWeight: 700 }}>
                  ✓
                </span>
                <span style={{ fontWeight: 560 }}>{line}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function Check() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" style={{ flex: "none", marginTop: "3px" }}>
      <circle cx="10" cy="10" r="10" fill="var(--teal-100)" />
      <path
        d="M5.5 10.2l3 3 6-6.4"
        stroke="var(--teal-700)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}

function HowItWorks() {
  const steps: [string, string][] = [
    ["Tell us about your year", "Your job, your family, anything that changed."],
    ["Add your documents", "Upload a W-2 and we read it. You check what we read."],
    ["See your refund", "Calculated as you go, with every figure explained."],
    ["Review and file", "Pay $14.99, sign, and track your return to acceptance."],
  ];

  return (
    <section className="shell" style={{ paddingBlock: "clamp(3rem, 7vw, 5rem)" }}>
      <h2 className="center">Four steps, in your own words</h2>
      <div
        style={{
          display: "grid",
          gap: "1.5rem",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          marginTop: "2.5rem",
        }}
      >
        {steps.map(([title, body], index) => (
          <div key={title}>
            <div
              aria-hidden="true"
              style={{
                width: "2.5rem",
                height: "2.5rem",
                borderRadius: "999px",
                background: "var(--teal-700)",
                color: "#fff",
                display: "grid",
                placeItems: "center",
                fontWeight: 700,
                marginBottom: "0.9rem",
              }}
            >
              {index + 1}
            </div>
            <h3 style={{ fontSize: "1.05rem" }}>{title}</h3>
            <p className="small muted">{body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Honesty() {
  return (
    <section
      style={{
        background: "var(--ink-900)",
        color: "var(--ink-050)",
        paddingBlock: "clamp(3rem, 7vw, 4.5rem)",
      }}
    >
      <div className="shell" style={{ maxWidth: "50rem" }}>
        <h2 style={{ color: "#fff" }}>What we will not do</h2>
        <div style={{ display: "grid", gap: "1.25rem", marginTop: "1.75rem" }}>
          {[
            [
              "We will not tell you a return is filed when it is not.",
              "Your return is only marked accepted when a taxing authority actually says so.",
            ],
            [
              "We will not guess at your taxes.",
              "If your situation needs something we have not built yet, we will tell you plainly instead of producing a number we cannot stand behind.",
            ],
            [
              "We will not hide what we cannot do.",
              "You can read exactly which situations we support before you enter anything.",
            ],
            [
              "We will not put your tax data into advertising tools.",
              "Your income, your dependents and your refund are not marketing data.",
            ],
          ].map(([title, body]) => (
            <div key={title}>
              <div style={{ fontWeight: 640, color: "#fff" }}>{title}</div>
              <p className="small" style={{ color: "var(--ink-300)", margin: "0.25rem 0 0" }}>
                {body}
              </p>
            </div>
          ))}
        </div>
        <Link
          className="btn btn--secondary"
          href="/what-we-support"
          style={{ marginTop: "2rem" }}
        >
          See what we support
        </Link>
      </div>
    </section>
  );
}

function ClosingCta() {
  return (
    <section className="shell center" style={{ paddingBlock: "clamp(3rem, 7vw, 5rem)" }}>
      <h2>Your taxes. One price. $14.99.</h2>
      <p className="muted" style={{ marginInline: "auto" }}>
        Start now and pay only when you decide to file.
      </p>
      <Link className="btn btn--primary" href="/start" style={{ marginTop: "1.25rem" }}>
        Start Filing
      </Link>
    </section>
  );
}
