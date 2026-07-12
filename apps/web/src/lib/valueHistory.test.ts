import { describe, expect, it } from "vitest";
import {
  depositsBaseline,
  hasBackfill,
  performanceExDeposits,
} from "./valueHistory";

const snap = (d: string, v: string, source?: string) => ({
  taken_on: d,
  total_value_usd: v,
  source,
});

const SNAPS = [
  snap("2026-07-01", "100000"),
  snap("2026-07-02", "101000"),
  snap("2026-07-05", "142000"),
  snap("2026-07-08", "141000"),
];
const FLOWS = [
  { occurred_on: "2026-06-15", net_usd: "5000" }, // before window: ignored
  { occurred_on: "2026-07-03", net_usd: "40000" }, // deposit mid-window
  { occurred_on: "2026-07-07", net_usd: "-2000" }, // withdrawal
];

describe("depositsBaseline", () => {
  it("steps by cumulative in-window flows from the first value", () => {
    expect(depositsBaseline(SNAPS, FLOWS)).toEqual([
      100000, 100000, 140000, 138000,
    ]);
  });
  it("null without flows in the window or with <2 snapshots", () => {
    expect(depositsBaseline(SNAPS, [])).toBeNull();
    expect(
      depositsBaseline(SNAPS, [{ occurred_on: "2026-06-01", net_usd: "9" }]),
    ).toBeNull();
    expect(depositsBaseline([SNAPS[0]], FLOWS)).toBeNull();
  });
});

describe("performanceExDeposits", () => {
  it("separates the deposit from the gain", () => {
    // last value 141000 vs baseline 138000: the $40k deposit isn't a gain,
    // real performance is +3000 (+2.17%)
    const p = performanceExDeposits(SNAPS, FLOWS)!;
    expect(p.usd).toBe(3000);
    expect(p.pct).toBeCloseTo(2.17, 1);
  });
});

describe("hasBackfill", () => {
  it("detects backfilled points", () => {
    expect(hasBackfill(SNAPS)).toBe(false);
    expect(hasBackfill([snap("2026-01-01", "1", "backfill_rh")])).toBe(true);
  });
});
