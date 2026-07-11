import { describe, expect, it } from "vitest";
import { filterByTag, groupExposure, themeTotals, type ExposureRow } from "./exposure";

const row = (u: string, stock: number, opt: number, tags?: string[]): ExposureRow => ({
  underlying: u,
  tags,
  stock_value_usd: stock.toFixed(2),
  option_value_usd: opt.toFixed(2),
  total_usd: (stock + opt).toFixed(2),
  weight_pct: "1.0000",
});

describe("groupExposure", () => {
  it("passes through when at or under the cap", () => {
    const rows = [row("A", 100, 0), row("B", 0, 50)];
    expect(groupExposure(rows, 12)).toEqual(rows);
  });
  it("folds the tail into Other with summed values", () => {
    const rows = [row("A", 100, 0), row("B", 50, 10), row("C", 5, 5), row("D", 1, 2)];
    const grouped = groupExposure(rows, 2);
    expect(grouped).toHaveLength(3);
    expect(grouped[2].underlying).toBe("Other (2)");
    expect(grouped[2].stock_value_usd).toBe("6.00");
    expect(grouped[2].option_value_usd).toBe("7.00");
    expect(grouped[2].total_usd).toBe("13.00");
  });
});

describe("filterByTag", () => {
  const rows = [row("A", 100, 0, ["ai", "semis"]), row("B", 50, 0, ["crypto"]), row("C", 10, 0)];
  it("no tag returns all rows", () => {
    expect(filterByTag(rows, null)).toHaveLength(3);
  });
  it("filters to rows carrying the tag; tagless rows never match", () => {
    expect(filterByTag(rows, "ai").map((r) => r.underlying)).toEqual(["A"]);
    expect(filterByTag(rows, "nope")).toHaveLength(0);
  });
});

describe("themeTotals", () => {
  it("sums |exposure| per tag with overlap, largest first", () => {
    const rows = [
      row("A", 100, 0, ["ai", "semis"]),
      row("B", 0, -40, ["ai"]), // short: abs counts
      row("C", 10, 0, ["crypto"]),
      row("D", 5, 0), // untagged contributes nowhere
    ];
    const totals = themeTotals(rows);
    expect(totals[0]).toEqual({ tag: "ai", total_usd: 140, count: 2 });
    expect(totals.map((t) => t.tag)).toEqual(["ai", "semis", "crypto"]);
  });
});
