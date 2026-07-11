// Shapes returned by the worker's internal portfolio API.
// All Decimals arrive as strings (see apps/worker/app/portfolio_api.py).

import { optionLabel } from "./format";

export type BrokerQty = { broker: string; qty: string };

/** Per-position basket allocation chip. Empty array (or absent, on a
 * worker that predates baskets) = fully core. */
export type BasketAllocation = { slug: string; qty: string };

export type PortfolioPosition = {
  symbol: string;
  sec_type: string; // STK | OPT | CASH
  qty: string;
  avg_cost_usd: string | null;
  last_price_usd: string | null;
  prev_close_usd: string | null;
  market_value_usd: string;
  unrealized_pl_usd: string;
  day_change_usd: string;
  weight_pct: string;
  expiry: string | null;
  strike: string | null;
  right: string | null;
  brokers: BrokerQty[];
  baskets?: BasketAllocation[];
};

export type PortfolioAccount = {
  broker: string;
  external_id: string;
  last_synced_at: string | null;
  stale: boolean;
};

export type Portfolio = {
  total_value_usd: string;
  day_change_usd: string;
  day_change_pct: string;
  cash_usd: string;
  accounts: PortfolioAccount[];
  positions: PortfolioPosition[];
};

export type PositionAccountRow = {
  broker: string;
  external_id: string;
  qty: string;
  avg_cost_usd: string | null;
  market_value_usd: string;
  unrealized_pl_usd: string;
};

export type PositionDetail = Omit<PortfolioPosition, "weight_pct" | "brokers"> & {
  accounts: PositionAccountRow[];
};

export type SnapshotPoint = { taken_on: string; total_value_usd: string };

// --- Baskets (GET /internal/baskets, GET /internal/baskets/{slug}) ---

export type Basket = {
  slug: string;
  name: string;
  thesis: string;
  status: string;
  deployed_usd: string;
  current_value_usd: string;
  pl_usd: string;
  pl_pct: string;
  nearest_expiry: string | null; // ISO date
  created_at: string;
};

export type BasketSnapshotPoint = { taken_on: string; value_usd: string };

export type BasketDetail = {
  slug: string;
  name: string;
  thesis: string;
  invalidation: string | null;
  horizon: string | null;
  source_ref: string | null;
  status: string;
  created_at: string;
  deployed_usd: string;
  current_value_usd: string;
  pl_usd: string;
  pl_pct: string;
  positions: PortfolioPosition[]; // scoped to allocation qty
  snapshots: BasketSnapshotPoint[]; // ascending by taken_on
};

/** Display label: ticker for equities, "AAPL $150 C 12/18" for options. */
export function positionLabel(p: {
  symbol: string;
  sec_type: string;
  expiry: string | null;
  strike: string | null;
  right: string | null;
}): string {
  if (p.sec_type === "OPT" && p.expiry && p.strike && p.right) {
    return optionLabel({
      symbol: p.symbol,
      expiry: p.expiry,
      strike: p.strike,
      right: p.right,
    });
  }
  return p.symbol;
}
