/**
 * Money formatting for the UI.
 *
 * Amounts arrive from the API as strings, not numbers, because the tax engine
 * works in exact decimals. They are passed to `Intl.NumberFormat` as strings
 * too: the Intl.NumberFormat v3 spec formats a string argument from its exact
 * decimal value, with no intermediate binary float. Runtimes predating v3
 * coerce the string with ToNumber, which is exactly what parsing it ourselves
 * would have done — so this is exact where supported and no worse anywhere
 * else.
 *
 * The previous version called `Number.parseFloat` first while carrying a
 * comment claiming to avoid float. For the magnitudes on a tax return the two
 * agree, so nothing was visibly wrong; the comment was still describing code
 * that did not exist, which is the kind of thing that gets trusted later by
 * someone reaching for a helper in a context where it does matter.
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
  return format(currency, value, "$0.00");
}

export function moneyWhole(value: string | number | null | undefined): string {
  return format(whole, value, "$0");
}

function format(
  formatter: Intl.NumberFormat,
  value: string | number | null | undefined,
  fallback: string,
): string {
  if (value === null || value === undefined || value === "") return fallback;
  try {
    // The string goes through untouched, so Intl formats it from its exact
    // decimal value.
    const formatted = formatter.format(value as number);
    // Intl does not throw on unparseable input -- it renders "$NaN". Showing a
    // taxpayer "$NaN" where a refund should be is worse than showing nothing,
    // so an unparseable amount falls back rather than being displayed.
    return formatted.includes("NaN") ? fallback : formatted;
  } catch {
    return fallback;
  }
}

export function isPositive(value: string | null | undefined): boolean {
  if (!value) return false;
  const amount = Number.parseFloat(value);
  return Number.isFinite(amount) && amount > 0;
}
