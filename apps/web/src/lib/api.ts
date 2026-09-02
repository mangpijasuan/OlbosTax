/**
 * Client for the OlbosTax API.
 *
 * Two rules shape this module.
 *
 * The access token is held in memory, never in `localStorage`. A token in
 * local storage is readable by any script that ends up on the page, and the
 * consequence here is not a hijacked shopping cart but access to a stranger's
 * Social Security number and the ability to redirect their refund. Holding it
 * in memory means a refresh signs the user out, which is the correct trade for
 * a tax filing session.
 *
 * Errors are surfaced as messages the API wrote, because the API is where the
 * plain-language wording lives. The UI does not invent its own explanations
 * for server-side failures; doing so is how two different sentences end up
 * describing the same condition.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface FilingReadiness {
  can_file: boolean;
  blockers: string[];
  rule_sets: {
    jurisdiction: string;
    tax_year: number;
    certification: string;
    filable: boolean;
  }[];
  efile_channel: string;
  price: string;
}

export interface CalculationBreakdownStep {
  code: string;
  label: string;
  amount: string;
  kind: string;
  jurisdiction: string;
  form_line: string | null;
  detail: string | null;
  rule_citation: string | null;
}

export interface CapabilityFinding {
  code: string;
  level: "SUPPORTED" | "PARTIALLY_SUPPORTED" | "NOT_SUPPORTED" | "REQUIRES_TAX_PROFESSIONAL";
  title: string;
  explanation: string;
  guidance: string;
}

export interface FederalResult {
  adjusted_gross_income: string;
  taxable_income: string;
  total_tax: string;
  refund: string;
  amount_owed: string;
  deduction_taken: string;
  deduction_is_itemized: boolean;
  required_forms: string[];
}

export interface OklahomaResult {
  oklahoma_adjusted_gross_income: string;
  oklahoma_taxable_income: string;
  total_tax: string;
  refund: string;
  amount_owed: string;
  required_forms: string[];
}

export interface Computation {
  federal: FederalResult | null;
  oklahoma: OklahomaResult | null;
  findings: CapabilityFinding[];
  breakdown: CalculationBreakdownStep[];
  can_be_filed: boolean;
  rule_sets_certified: boolean;
}

export interface ReturnSummary {
  id: string;
  tax_year: number;
  status: string;
  version_number: number;
  federal_refund: string | null;
  federal_amount_owed: string | null;
  oklahoma_refund: string | null;
  oklahoma_amount_owed: string | null;
  can_be_filed: boolean;
  updated_at: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly correlationId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    // Tax responses must not sit in any cache, including the browser's.
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = "Something went wrong. Please try again.";
    let correlationId: string | undefined;
    try {
      const body = await response.json();
      // FastAPI validation errors arrive as an array of field problems; a
      // taxpayer should never see that shape, so it is collapsed into the one
      // sentence the field-level UI can attach to.
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        detail = "Please check the highlighted fields and try again.";
      }
      correlationId = body.correlation_id;
    } catch {
      // A non-JSON error body (a gateway timeout page, say) leaves the
      // default message in place.
    }
    throw new ApiError(detail, response.status, correlationId);
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/**
 * Server-side fetch of filing readiness.
 *
 * Returns null rather than throwing when the API is unreachable: this is
 * called during the render of a public marketing page, and an API outage
 * should not take that page down with it.
 */
export async function getFilingReadiness(): Promise<FilingReadiness | null> {
  try {
    const response = await fetch(`${API_BASE}/api/v1/system/filing-readiness`, {
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as FilingReadiness;
  } catch {
    return null;
  }
}

export const api = {
  register: (email: string, password: string) =>
    request<{ message: string }>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string, totpCode?: string) =>
    request<{
      access_token: string;
      expires_at: string;
      mfa_required: boolean;
      mfa_enabled: boolean;
    }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, totp_code: totpCode ?? null }),
    }),

  logout: () => request<{ message: string }>("/api/v1/auth/logout", { method: "POST" }),

  listReturns: () => request<ReturnSummary[]>("/api/v1/returns"),

  getReturn: (id: string) =>
    request<{
      id: string;
      tax_year: number;
      version_id: string;
      version_number: number;
      status: string;
      return_input: Record<string, unknown>;
    }>(`/api/v1/returns/${id}`),

  saveReturn: (id: string, returnInput: unknown) =>
    request<{ version_id: string; computation: Computation }>(`/api/v1/returns/${id}`, {
      method: "PUT",
      body: JSON.stringify({ return_input: returnInput }),
    }),

  /**
   * The stored calculation for a return, without modifying it.
   *
   * Used for returns that are finalized and therefore read-only. The API
   * returns the snapshot taken when the return was computed, including the
   * engine and rule versions in force at the time -- which is what should be
   * displayed for a filed return, rather than what today's engine would make
   * of the same inputs.
   */
  getStoredComputation: async (id: string): Promise<Computation> => {
    const stored = await request<{
      federal: FederalResult | null;
      oklahoma: OklahomaResult | null;
      findings: CapabilityFinding[];
      trace: { steps: CalculationBreakdownStep[] };
      rule_sets_certified: boolean;
    }>(`/api/v1/returns/${id}/calculation`);

    const shown = new Set(["RESULT", "LIMITATION", "SUBTOTAL"]);
    return {
      federal: stored.federal,
      oklahoma: stored.oklahoma,
      findings: stored.findings ?? [],
      breakdown: (stored.trace?.steps ?? []).filter((step) => shown.has(step.kind)),
      // A filed return is not re-offered for filing, so this is reported as
      // stored rather than recomputed.
      can_be_filed: false,
      rule_sets_certified: stored.rule_sets_certified,
    };
  },

  checkout: (returnId: string) =>
    request<{
      payment_id: string;
      amount_cents: number;
      amount_display: string;
      description: string;
    }>("/api/v1/payments/checkout", {
      method: "POST",
      body: JSON.stringify({ return_id: returnId }),
    }),

  capabilityMatrix: () =>
    request<Record<string, { level: string; description: string }>>(
      "/api/v1/returns/capability-matrix",
    ),

  validationCoverage: () =>
    request<{
      implemented: { level: string; name: string; status: string }[];
      not_implemented: {
        level: string;
        name: string;
        status: string;
        reason: string;
        consequence: string;
      }[];
    }>("/api/v1/efile/validation-coverage"),
};
