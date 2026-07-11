// Formatting + masking helpers. Masking semantics live here (and only here):
// `display(value, masked)` hides dollar amounts and `displayQty(value, masked)`
// hides share counts (qty x public price = dollars); percents are always real.

const usdFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "$1,234.50" — grouping, cents, leading minus on negatives. */
export function usd(value: string | number): string {
  return usdFmt.format(Number(value));
}

/** "12.35%" — two decimals, sign preserved. */
export function pct(value: string | number): string {
  return `${Number(value).toFixed(2)}%`;
}

export const MASK = "•••";

/** Dollar display: real via usd() unless masked, then bullets. */
export function display(value: string | number, masked: boolean): string {
  return masked ? MASK : usd(value);
}

const qtyFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 8, // RH fractional shares go to 6 dp; keep headroom
});

/** "1,234.5" — grouping, fractional part kept, no padded zeros. */
export function qty(value: string | number): string {
  return qtyFmt.format(Number(value));
}

/** Quantity display: real via qty() unless masked, then bullets. Share
 * counts let a viewer reconstruct absolute dollar values from public
 * per-share prices, so masked users never see them. */
export function displayQty(value: string | number, masked: boolean): string {
  return masked ? MASK : qty(value);
}

/** Trailing OCC tail: YYMMDD + C/P + strike*1000 zero-padded to 8. */
const OCC_TAIL = /\d{6}[CP]\d{8}$/;

function strikeLabel(strike: string): string {
  const n = Number(strike);
  return Number.isInteger(n) ? `$${n}` : `$${n.toFixed(2)}`;
}

function expiryLabel(expiry: string): string {
  // API sends ISO dates (YYYY-MM-DD); avoid Date() to dodge TZ shifts.
  const [, m, d] = expiry.split("-");
  return `${Number(m)}/${Number(d)}`;
}

/** "AAPL $150 C 12/18" from the API's OCC-style option fields. */
export function optionLabel(opt: {
  symbol: string;
  expiry: string;
  strike: string;
  right: string;
}): string {
  const underlying = opt.symbol.replace(OCC_TAIL, "");
  return `${underlying} ${strikeLabel(opt.strike)} ${opt.right} ${expiryLabel(opt.expiry)}`;
}
