// Shapes + pure helpers for the flow-adjusted performance section.
// Mirrors GET /internal/performance (apps/worker/app/portfolio_api.py).
// All money is a Decimal string; percentages are decimal-percent strings
// (e.g. "42.35") or null when undefined.

export type PerfValuePoint = {
  date: string; // ISO YYYY-MM-DD
  value_usd: string;
  estimated: boolean;
};

export type PerfContribPoint = {
  date: string;
  value_usd: string;
};

// Series + current value ship on every response (even an unavailable period,
// so the chart still renders). Headline metrics are present only when
// `available` is true.
export type Performance = {
  period: string;
  current_value_usd: string;
  value_series: PerfValuePoint[];
  contributions_series: PerfContribPoint[];
  available: boolean;
  reason?: string; // present when available === false
  // present when available === true:
  headline_metric?: "annualized" | "cumulative";
  mwr_annualized_pct?: string | null;
  cumulative_return_pct?: string | null;
  twr_pct?: string | null;
  dollar_pnl_usd?: string;
  net_contributions_usd?: string;
  solid?: boolean;
  boundary_estimated?: boolean;
  caveats?: string[];
};

export type Period = "inception" | "1y" | "ytd";

export const PERIODS: { id: Period; label: string }[] = [
  { id: "inception", label: "Since inception" },
  { id: "1y", label: "1Y" },
  { id: "ytd", label: "YTD" },
];

/** Index of the first OBSERVED (non-estimated) value point. Everything before
 * it is the reconstructed pre-go-live tail (drawn distinct). Returns
 * `series.length` when every point is estimated, `0` when none are. The split
 * point itself belongs to BOTH segments so the line stays visually continuous. */
export function firstObservedIndex(series: { estimated: boolean }[]): number {
  const i = series.findIndex((p) => !p.estimated);
  return i === -1 ? series.length : i;
}
