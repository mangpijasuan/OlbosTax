/**
 * Money formatting for the UI.
 *
 * Amounts arrive from the API as strings, not numbers, and are kept as strings
 * until the moment they are displayed. The API sends strings because the tax
 * engine works in exact decimals, and parsing one into a JavaScript number
 * reintroduces binary floating point at the very last step -- which is how a
 * refund of $2,183.40 renders as $2,183.3999999999996.
 *
 * `Intl.NumberFormat` accepts a string only via the newer overloads, so the
 * conversion happens here, once, where the rounding behaviour is visible.
 */

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const whole = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "$0.00";
  const amount = typeof value === "string" ? Number.parseFloat(value) : value;
  return Number.isFinite(amount) ? currency.format(amount) : "$0.00";
}

export function moneyWhole(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "$0";
  const amount = typeof value === "string" ? Number.parseFloat(value) : value;
  return Number.isFinite(amount) ? whole.format(amount) : "$0";
}

export function isPositive(value: string | null | undefined): boolean {
  if (!value) return false;
  const amount = Number.parseFloat(value);
  return Number.isFinite(amount) && amount > 0;
}
