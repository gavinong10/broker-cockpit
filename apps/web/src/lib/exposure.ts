// Exposure-by-underlying: types + display grouping for the /exposure chart.

export type ExposureConstituent = {
  symbol: string;
  sec_type: "STK" | "OPT";
  expiry: string | null;
  strike: string | null;
  right: string | null;
  qty: string;
  market_value_usd: string;
  baskets: { slug: string; qty: string }[];
};

export type ExposureRow = {
  underlying: string;
  /** Theme tags inherited by every position on this underlying (absent on
   * older workers and on the synthetic "Other" row). */
  tags?: string[];
  stock_value_usd: string;
  option_value_usd: string;
  total_usd: string;
  weight_pct: string;
  /** Per-position breakdown (absent on the synthetic "Other" row). */
  positions?: ExposureConstituent[];
  /** On the synthetic "Other" row only: the folded underlyings, for expansion. */
  others?: ExposureRow[];
};

export const EXPOSURE_TOP_N = 12;

/** Rows matching a theme tag (no tag -> all rows). */
export function filterByTag(rows: ExposureRow[], tag: string | null): ExposureRow[] {
  if (!tag) return rows;
  return rows.filter((r) => (r.tags ?? []).includes(tag));
}

export type ThemeTotal = { tag: string; total_usd: number; count: number };

/** Aggregate |exposure| per theme tag, largest first. Multi-tagged
 * underlyings count toward every tag they carry, so themes OVERLAP —
 * totals are per-theme lenses, not a partition of the portfolio. */
export function themeTotals(rows: ExposureRow[]): ThemeTotal[] {
  const acc = new Map<string, ThemeTotal>();
  for (const r of rows) {
    for (const tag of r.tags ?? []) {
      const t = acc.get(tag) ?? { tag, total_usd: 0, count: 0 };
      t.total_usd += Math.abs(Number(r.total_usd));
      t.count += 1;
      acc.set(tag, t);
    }
  }
  return [...acc.values()].sort((a, b) => b.total_usd - a.total_usd);
}

/** Top-N rows by |total|, remainder folded into one "Other" row (display-only
 * grouping per the dataviz rule: a 13th bar is never a new hue/row). The
 * folded rows ride along on `others` so the UI can expand them. */
export function groupExposure(rows: ExposureRow[], topN: number = EXPOSURE_TOP_N): ExposureRow[] {
  if (rows.length <= topN) return rows;
  const head = rows.slice(0, topN);
  const rest = rows.slice(topN);
  const sum = (k: keyof Pick<ExposureRow, "stock_value_usd" | "option_value_usd" | "total_usd" | "weight_pct">) =>
    rest.reduce((a, r) => a + Number(r[k]), 0);
  return [
    ...head,
    {
      underlying: `Other (${rest.length})`,
      stock_value_usd: sum("stock_value_usd").toFixed(2),
      option_value_usd: sum("option_value_usd").toFixed(2),
      total_usd: sum("total_usd").toFixed(2),
      weight_pct: sum("weight_pct").toFixed(4),
      others: rest,
    },
  ];
}
