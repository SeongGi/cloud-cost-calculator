// Money math in integer cents with BigInt: no floating point anywhere in a total.
// Unit prices are decimal strings with up to 10 fraction digits (as in the catalogs).

const SCALE = 10_000_000_000n; // 10^10
const CENT_DIVISOR = 100_000_000n; // 10^10 / 100
const HALF = CENT_DIVISOR / 2n;

export function toScaled(price: string): bigint {
  const clean = price.trim();
  if (!/^\d+(\.\d+)?$/.test(clean)) throw new Error(`invalid price: ${price}`);
  const [intPart, frac = ""] = clean.split(".");
  return BigInt(intPart) * SCALE + BigInt(frac.slice(0, 10).padEnd(10, "0"));
}

/** round_half_up(price x scale x hours x quantity x 100), matching the agent's Decimal math. */
export function subtotalCents(price: string, scale: number, hours: number, quantity: number): bigint {
  if (quantity <= 0) return 0n;
  const n = toScaled(price) * BigInt(Math.round(scale)) * BigInt(Math.round(hours)) * BigInt(Math.round(quantity));
  return (n + HALF) / CENT_DIVISOR;
}

export function formatUsd(cents: bigint): string {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const dollars = (abs / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}$${dollars}.${(abs % 100n).toString().padStart(2, "0")}`;
}

/** "$0.0520" style: trailing zeros removed, at least two decimals. */
export function formatUnitPrice(price: string): string {
  const [intPart, frac = ""] = price.trim().split(".");
  return `$${intPart}.${frac.replace(/0+$/, "").padEnd(2, "0")}`;
}

/** Price per displayed quantity unit (e.g. per million requests) as an exact decimal string. */
export function pricePerUnit(price: string, scale: number): string {
  const n = toScaled(price) * BigInt(Math.round(scale));
  const intPart = n / SCALE;
  const frac = (n % SCALE).toString().padStart(10, "0");
  return `${intPart}.${frac}`;
}

/** Whole number of at least the slider minimum (up to 9 digits), else null. The slider's
 * max and step only shape the slider; typed values may go beyond them. */
export function parseQuantity(input: string, slider: { min: number }): number | null {
  const text = input.trim();
  if (!/^\d{1,9}$/.test(text)) return null;
  const value = Number(text);
  return value < slider.min ? null : value;
}
