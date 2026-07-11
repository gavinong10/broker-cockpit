// Pure helpers for the basket Plan section (pending purchases, monitoring).
// Kept out of components so they get vitest coverage.

export type PlanMark = {
  taken_at: string;
  net_cost: string | null;
  underlying_spot: string | null;
  quote_basis: string | null;
};

export type PlanLeg = {
  label: string;
  structure: { occ?: string; symbol?: string; sec_type: string; ratio: number }[];
  qty: string;
  planned_net_debit: string;
  tolerance_pct: string;
  breakeven_underlying: string | null;
  max_value_usd: string | null;
  thesis_note: string | null;
  status: string; // pending | partial | held | abandoned
  monitor_status: string | null; // in_window | drifted | thesis_stale | unquotable
  last_quote_net: string | null;
  last_quote_delta_pct: string | null;
  last_quoted_at: string | null;
  filled_net_debit: string | null;
  created_at: string;
  marks: PlanMark[];
};

export type PlanView = {
  slug: string;
  legs: PlanLeg[];
  planned_total_usd: string;
  payoff_curve: { move_pct: string; pnl_usd: string }[];
  curve_excluded: string[];
};

export type ChipTone = "gain" | "amber" | "loss" | "muted" | "accent";

/** Status chip for a leg: lifecycle first (held/partial/abandoned), then the
 * live monitor grade for pending legs. */
export function legChip(leg: {
  status: string;
  monitor_status: string | null;
}): { text: string; tone: ChipTone } {
  if (leg.status === "held") return { text: "filled", tone: "accent" };
  if (leg.status === "abandoned") return { text: "abandoned", tone: "muted" };
  const prefix = leg.status === "partial" ? "partial · " : "";
  switch (leg.monitor_status) {
    case "in_window":
      return { text: `${prefix}in window`, tone: "gain" };
    case "drifted":
      return { text: `${prefix}drifted`, tone: "amber" };
    case "thesis_stale":
      return { text: `${prefix}thesis stale`, tone: "loss" };
    case "unquotable":
      return { text: `${prefix}no quote`, tone: "muted" };
    default:
      return { text: `${prefix}awaiting first check`, tone: "muted" };
  }
}

/** Slippage of a filled leg vs plan, in percent (negative = filled cheaper). */
export function fillSlippagePct(leg: {
  planned_net_debit: string;
  filled_net_debit: string | null;
}): number | null {
  if (leg.filled_net_debit === null) return null;
  const planned = Number(leg.planned_net_debit);
  const filled = Number(leg.filled_net_debit);
  if (!planned || !Number.isFinite(filled)) return null;
  return (filled / planned - 1) * 100;
}

/** SVG polyline points for a mark-history sparkline (net cost over time),
 * with the planned level as reference. Returns null when fewer than two
 * quotable marks exist. */
export function sparklinePoints(
  marks: PlanMark[],
  planned: number,
  width = 120,
  height = 32,
): { points: string; plannedY: number } | null {
  const nets = marks
    .filter((m) => m.net_cost !== null)
    .map((m) => Number(m.net_cost));
  if (nets.length < 2) return null;
  const all = [...nets, planned];
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const y = (v: number) => height - ((v - min) / span) * height;
  const step = width / (nets.length - 1);
  const points = nets.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return { points, plannedY: y(planned) };
}

/** Scale payoff-curve points into an SVG path plus zero-line position.
 * Returns null for fewer than two points. */
export function payoffPath(
  curve: { move_pct: string; pnl_usd: string }[],
  width = 560,
  height = 160,
): { path: string; zeroY: number; xZeroPct: number } | null {
  if (curve.length < 2) return null;
  const moves = curve.map((p) => Number(p.move_pct));
  const pnls = curve.map((p) => Number(p.pnl_usd));
  const xMin = Math.min(...moves);
  const xMax = Math.max(...moves);
  const yMin = Math.min(...pnls, 0);
  const yMax = Math.max(...pnls, 0);
  const xSpan = xMax - xMin || 1;
  const ySpan = yMax - yMin || 1;
  const px = (m: number) => ((m - xMin) / xSpan) * width;
  const py = (v: number) => height - ((v - yMin) / ySpan) * height;
  const path = curve
    .map((p, i) => `${i === 0 ? "M" : "L"}${px(Number(p.move_pct)).toFixed(1)} ${py(Number(p.pnl_usd)).toFixed(1)}`)
    .join(" ");
  return {
    path,
    zeroY: py(0),
    xZeroPct: ((0 - xMin) / xSpan) * 100,
  };
}

/** One-line structure summary: "220C/330C x2 Dec 15 2028" style. */
export function structureSummary(leg: PlanLeg): string {
  const parts = leg.structure.map((c) => {
    if (c.sec_type === "STK") return `${c.symbol} x${c.ratio}`;
    const occ = c.occ ?? "";
    const m = occ.match(/^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/);
    if (!m) return occ;
    const strike = Number(m[6]) / 1000;
    return `${strike}${m[5]}${c.ratio < 0 ? " short" : ""}`;
  });
  const occ = leg.structure.find((c) => c.occ)?.occ;
  const em = occ?.match(/^[A-Z]{1,6}(\d{2})(\d{2})(\d{2})/);
  const expiry = em ? ` · exp 20${em[1]}-${em[2]}-${em[3]}` : "";
  return `${parts.join(" / ")} ×${Number(leg.qty)}${expiry}`;
}
