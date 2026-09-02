import { getFilingReadiness } from "@/lib/api";

/**
 * States plainly, on the landing page, whether OlbosTax can file a return.
 *
 * This is the one component on the marketing page that could be argued out of
 * existence by a growth team, so the reason it exists is worth stating: a
 * taxpayer who spends an hour entering their W-2 and then discovers at
 * checkout that filing is unavailable has been misled, whether or not any
 * individual sentence on the page was false. The readiness endpoint is the
 * source of truth, so this banner disappears by itself when the blockers
 * clear -- nobody has to remember to remove it.
 */
export async function FilingReadinessBanner() {
  const readiness = await getFilingReadiness();

  if (!readiness) {
    // The API is unreachable. Say nothing rather than guessing: claiming
    // either "we can file" or "we cannot" without knowing would be worse than
    // silence, and the start flow re-checks before taking any payment.
    return null;
  }

  if (readiness.can_file) {
    return null;
  }

  return (
    <div className="notice notice--warn" style={{ marginTop: "2rem" }}>
      <span className="notice__icon" aria-hidden="true">
        ⏳
      </span>
      <div className="notice__body">
        <p className="notice__title">Filing is not open yet for this tax year</p>
        <p className="small">
          You can prepare your return now and see what your refund looks like.
          You cannot submit it yet, and we will not charge you until you can.
        </p>
        <ul className="small" style={{ margin: "0.5rem 0 0", paddingLeft: "1.2rem" }}>
          {readiness.blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
