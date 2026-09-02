import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OlbosTax — File your taxes for $14.99",
  description:
    "Federal and Oklahoma tax filing for one flat price. No upgrades, no surprise fees, and no percentage of your refund.",
  // Tax pages carry taxpayer data and have no business in a search index.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Deliberately no maximumScale or userScalable:false. Blocking zoom on a
  // form people fill in on a phone is an accessibility failure, and it is the
  // single most common one on financial sites.
  themeColor: "#0a5c60",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
