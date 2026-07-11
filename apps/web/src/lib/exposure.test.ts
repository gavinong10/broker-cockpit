import { describe, expect, it } from "vitest";
import { groupExposure, type ExposureRow } from "./exposure";

const row = (u: string, stock: number, opt: number): ExposureRow => ({
  underlying: u,
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
