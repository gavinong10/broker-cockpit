// Exposure-by-underlying: types + display grouping for the /exposure chart.

export type ExposureRow = {
  underlying: string;
  stock_value_usd: string;
  option_value_usd: string;
  total_usd: string;
  weight_pct: string;
};

export const EXPOSURE_TOP_N = 12;

/** Top-N rows by |total|, remainder folded into one "Other" row (display-only
 * grouping per the dataviz rule: a 13th bar is never a new hue/row). */
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
    },
  ];
}
