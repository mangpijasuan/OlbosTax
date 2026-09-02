import Link from "next/link";

export function SiteHeader({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <header className="site-header">
      <div className="shell site-header__inner">
        <Link href="/" className="site-header__brand">
          <Wordmark />
        </Link>

        <nav className="site-header__nav">
          {signedIn ? (
            <>
              <Link className="btn btn--ghost" href="/dashboard">
                My returns
              </Link>
              <Link className="btn btn--ghost site-header__secondary" href="/account">
                Account
              </Link>
            </>
          ) : (
            <>
              <Link className="btn btn--ghost site-header__secondary" href="/how-it-works">
                How it works
              </Link>
              <Link className="btn btn--ghost site-header__secondary" href="/sign-in">
                Sign in
              </Link>
              <Link className="btn btn--primary" href="/start">
                Start filing
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}

function Wordmark() {
  return (
    <>
      <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
        <rect width="26" height="26" rx="7" fill="var(--teal-700)" />
        <path
          d="M7 13.5l3.6 3.6L19 8.7"
          stroke="#fff"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
      </svg>
      <span>
        Olbos<span style={{ color: "var(--teal-700)" }}>Tax</span>
      </span>
    </>
  );
}

export function SiteFooter() {
  return (
    <footer
      style={{
        borderTop: "1px solid var(--ink-100)",
        background: "var(--paper-warm)",
        marginTop: "5rem",
        paddingBlock: "3rem 2.5rem",
      }}
    >
      <div className="shell">
        <div
          style={{
            display: "grid",
            gap: "2rem",
            gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          }}
        >
          <div>
            <div style={{ fontWeight: 700, marginBottom: "0.5rem" }}>OlbosTax</div>
            <p className="small muted" style={{ maxWidth: "24ch" }}>
              Tax preparation software from Olbos Technologies, LLC.
            </p>
          </div>
          <FooterColumn
            title="Product"
            links={[
              ["How it works", "/how-it-works"],
              ["Pricing", "/#pricing"],
              ["What we support", "/what-we-support"],
            ]}
          />
          <FooterColumn
            title="Legal"
            links={[
              ["Terms of service", "/legal/terms"],
              ["Privacy policy", "/legal/privacy"],
              ["Security", "/legal/security"],
            ]}
          />
          <FooterColumn
            title="Support"
            links={[
              ["Help centre", "/help"],
              ["Contact us", "/help/contact"],
            ]}
          />
        </div>

        {/*
          Required disclosure, in the footer of every page rather than buried
          in the terms. Spec section 45 asks for language making clear that
          OlbosTax is software and the taxpayer is responsible for reviewing
          their return; sections 6 and 47 forbid implying any government
          affiliation or approval we do not have.
        */}
        <div
          style={{
            marginTop: "2.5rem",
            paddingTop: "1.5rem",
            borderTop: "1px solid var(--ink-100)",
          }}
        >
          <p className="small muted" style={{ maxWidth: "80ch" }}>
            OlbosTax is tax preparation software. It is not a tax adviser, an
            accountant, or a law firm, and it does not provide tax or legal advice.
            You are responsible for reviewing your return for accuracy and
            completeness before you authorise it to be filed.
          </p>
          <p className="small muted" style={{ maxWidth: "80ch" }}>
            OlbosTax is not affiliated with, endorsed by, or approved by the
            Internal Revenue Service, the Oklahoma Tax Commission, or any other
            government agency.
          </p>
          <p className="small muted">
            © {new Date().getFullYear()} Olbos Technologies, LLC.
          </p>
        </div>
      </div>
    </footer>
  );
}

function FooterColumn({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <div>
      <div style={{ fontWeight: 640, marginBottom: "0.6rem" }}>{title}</div>
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "0.4rem" }}>
        {links.map(([label, href]) => (
          <li key={href}>
            <Link className="small" href={href} style={{ color: "var(--ink-700)" }}>
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
