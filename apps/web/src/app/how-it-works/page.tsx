import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HowItWorksPage() {
  let coverage: Awaited<ReturnType<typeof api.validationCoverage>> | null = null;
  try {
    coverage = await api.validationCoverage();
  } catch {
    coverage = null;
  }

  return (
    <>
      <SiteHeader />
      <main id="main" className="shell" style={{ paddingBlock: "3rem 1rem", maxWidth: "50rem" }}>
        <h1>How OlbosTax works</h1>
        <p className="muted">
          The short version: you tell us about your year, we do the arithmetic
          and show our working, and nothing is filed until you have read it and
          said yes.
        </p>

        <Section
          title="1. We ask about your life, not about form numbers"
          body="You will not be asked to find line 11 of Schedule 1. You will be asked whether you paid interest on a student loan. We work out which forms that means."
        />
        <Section
          title="2. Your documents are read, and then you check them"
          body="Upload a W-2 and we read the boxes. We then show you what we read next to what it means, and you confirm it. Software reading a number off an image is a guess, however confident it looks, and you are the one signing the return."
        />
        <Section
          title="3. The arithmetic is deterministic and explained"
          body="Your tax is calculated by a fixed set of rules, not by a language model. Every figure on your return can be traced back through the steps that produced it, against the line of the form it lands on."
        />
        <Section
          title="4. We check your return before you file it"
          body="We look for the mistakes that get returns rejected: numbers entered in the wrong box, a dependent's details that do not match, a refund with nowhere to go."
        />
        <Section
          title="5. You pay once, at the end"
          body="$14.99, after you have seen your refund and decided to file. Not before, and not more because your return turned out to be complicated."
        />

        {coverage && (
          <section style={{ marginTop: "3rem" }}>
            <h2>What our checks do and do not cover</h2>
            <p className="small muted">
              Being specific about this matters more than looking thorough.
            </p>

            <ul style={{ listStyle: "none", padding: 0, margin: "1.25rem 0 0", display: "grid", gap: "0.6rem" }}>
              {coverage.implemented.map((item) => (
                <li key={item.level} style={{ display: "flex", gap: "0.7rem" }}>
                  <span aria-hidden="true" style={{ color: "var(--money)", fontWeight: 700 }}>
                    ✓
                  </span>
                  <span>{item.name}</span>
                </li>
              ))}
            </ul>

            {coverage.not_implemented.length > 0 && (
              <div className="notice notice--warn" style={{ marginTop: "1.5rem" }}>
                <span className="notice__icon" aria-hidden="true">
                  ⚠
                </span>
                <div className="notice__body">
                  <p className="notice__title">Checks we have not built yet</p>
                  {coverage.not_implemented.map((item) => (
                    <p className="small" key={item.level}>
                      <strong>{item.name}.</strong> {item.consequence}
                    </p>
                  ))}
                </div>
              </div>
            )}
          </section>
        )}

        <p style={{ marginTop: "2.5rem" }}>
          <Link href="/start" className="btn btn--primary">
            Start Filing
          </Link>
        </p>
      </main>
      <SiteFooter />
    </>
  );
}

function Section({ title, body }: { title: string; body: string }) {
  return (
    <section style={{ marginTop: "2rem" }}>
      <h2 style={{ fontSize: "1.25rem" }}>{title}</h2>
      <p>{body}</p>
    </section>
  );
}
