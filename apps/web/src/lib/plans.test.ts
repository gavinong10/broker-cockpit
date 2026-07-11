import { describe, expect, it } from "vitest";
import {
  fillSlippagePct,
  legChip,
  payoffPath,
  sparklinePoints,
  structureSummary,
  type PlanLeg,
  type PlanMark,
} from "./plans";

const VERTICAL_LEG: PlanLeg = {
  label: "NBIS Dec-28 220/330",
  structure: [
    { occ: "NBIS281215C00220000", sec_type: "OPT", ratio: 1 },
    { occ: "NBIS281215C00330000", sec_type: "OPT", ratio: -1 },
  ],
  qty: "2",
  planned_net_debit: "17.23",
  tolerance_pct: "5.000",
  breakeven_underlying: null,
  max_value_usd: null,
  thesis_note: null,
  status: "pending",
  monitor_status: null,
  last_quote_net: null,
  last_quote_delta_pct: null,
  last_quoted_at: null,
  filled_net_debit: null,
  created_at: "2026-07-11T00:00:00Z",
  marks: [],
};

describe("legChip", () => {
  it("lifecycle outranks monitor status", () => {
    expect(legChip({ status: "held", monitor_status: "drifted" })).toEqual({
      text: "filled",
      tone: "accent",
    });
    expect(legChip({ status: "abandoned", monitor_status: null }).tone).toBe(
      "muted",
    );
  });
  it("grades pending legs by monitor status", () => {
    expect(legChip({ status: "pending", monitor_status: "in_window" }).tone).toBe("gain");
    expect(legChip({ status: "pending", monitor_status: "drifted" }).tone).toBe("amber");
    expect(legChip({ status: "pending", monitor_status: "thesis_stale" }).tone).toBe("loss");
    expect(legChip({ status: "pending", monitor_status: null }).text).toContain("awaiting");
  });
  it("prefixes partial fills", () => {
    expect(legChip({ status: "partial", monitor_status: "in_window" }).text).toBe(
      "partial · in window",
    );
  });
});

describe("fillSlippagePct", () => {
  it("is null until filled", () => {
    expect(fillSlippagePct(VERTICAL_LEG)).toBeNull();
  });
  it("computes signed slippage", () => {
    const filled = { ...VERTICAL_LEG, filled_net_debit: "16.50" };
    expect(fillSlippagePct(filled)!).toBeCloseTo(-4.24, 1);
  });
});

describe("sparklinePoints", () => {
  const mark = (net: string | null): PlanMark => ({
    taken_at: "2026-07-11T00:00:00Z",
    net_cost: net,
    underlying_spot: null,
    quote_basis: net === null ? null : "mid",
  });
  it("needs two quotable marks", () => {
    expect(sparklinePoints([mark("17.0")], 17.23)).toBeNull();
    expect(sparklinePoints([mark(null), mark("17.0")], 17.23)).toBeNull();
  });
  it("scales points and planned reference into the viewbox", () => {
    const s = sparklinePoints([mark("16.0"), mark("18.0")], 17.0, 100, 30)!;
    const pts = s.points.split(" ").map((p) => p.split(",").map(Number));
    expect(pts).toHaveLength(2);
    expect(pts[0][0]).toBe(0);
    expect(pts[1][0]).toBe(100);
    expect(pts[0][1]).toBeGreaterThan(pts[1][1]); // higher cost = lower y? no: 16 < 18 so first point lower value => larger y
    expect(s.plannedY).toBeGreaterThan(0);
    expect(s.plannedY).toBeLessThan(30);
  });
});

describe("payoffPath", () => {
  it("needs two points", () => {
    expect(payoffPath([{ move_pct: "0", pnl_usd: "0" }])).toBeNull();
  });
  it("spans the viewbox and places the zero line", () => {
    const g = payoffPath(
      [
        { move_pct: "-50", pnl_usd: "-3446" },
        { move_pct: "0", pnl_usd: "-3446" },
        { move_pct: "50", pnl_usd: "18554" },
        { move_pct: "120", pnl_usd: "18554" },
      ],
      560,
      160,
    )!;
    expect(g.path.startsWith("M0.0 160.0")).toBe(true); // min pnl at left edge, bottom
    expect(g.zeroY).toBeGreaterThan(0);
    expect(g.zeroY).toBeLessThan(160);
    // x zero sits at 50/170 of the range
    expect(g.xZeroPct).toBeCloseTo((50 / 170) * 100, 1);
  });
});

describe("structureSummary", () => {
  it("renders strikes, short legs, qty and expiry", () => {
    const s = structureSummary(VERTICAL_LEG);
    expect(s).toContain("220C");
    expect(s).toContain("330C short");
    expect(s).toContain("×2");
    expect(s).toContain("exp 2028-12-15");
  });
  it("renders stock legs", () => {
    const stock: PlanLeg = {
      ...VERTICAL_LEG,
      structure: [{ symbol: "NBIS", sec_type: "STK", ratio: 1 }],
    };
    expect(structureSummary(stock)).toContain("NBIS x1");
  });
});
