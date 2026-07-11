import { describe, expect, it } from "vitest";
import { usd, pct, display, displayQty, qty, optionLabel } from "./format";

describe("usd", () => {
  it("groups thousands and shows cents", () => {
    expect(usd("1234.5")).toBe("$1,234.50");
    expect(usd("1234567.891")).toBe("$1,234,567.89");
  });
  it("formats negatives with a leading minus", () => {
    expect(usd("-1234.5")).toBe("-$1,234.50");
    expect(usd(-0.4)).toBe("-$0.40");
  });
  it("formats zero and accepts numbers", () => {
    expect(usd(0)).toBe("$0.00");
    expect(usd("0")).toBe("$0.00");
  });
});

describe("pct", () => {
  it("renders two decimals with a percent sign", () => {
    expect(pct("12.3456")).toBe("12.35%");
    expect(pct(0)).toBe("0.00%");
  });
  it("keeps the sign on negatives", () => {
    expect(pct("-0.5")).toBe("-0.50%");
  });
});

describe("display", () => {
  it("returns real dollars when not masked", () => {
    expect(display("1234.5", false)).toBe("$1,234.50");
  });
  it("masks dollar values with bullets", () => {
    expect(display("1234.5", true)).toBe("•••");
    expect(display("-99", true)).toBe("•••");
  });
});

describe("qty", () => {
  it("formats whole quantities with grouping and no decimals", () => {
    expect(qty("10")).toBe("10");
    expect(qty("1234")).toBe("1,234");
  });
  it("keeps fractional shares without padding zeros", () => {
    expect(qty("10.500000")).toBe("10.5");
    expect(qty("0.123456")).toBe("0.123456");
  });
  it("keeps the sign on short positions", () => {
    expect(qty("-2")).toBe("-2");
  });
});

describe("displayQty", () => {
  it("returns the formatted quantity when not masked", () => {
    expect(displayQty("1234.5", false)).toBe("1,234.5");
    expect(displayQty("3", false)).toBe("3");
  });
  it("masks quantities with bullets", () => {
    expect(displayQty("1234.5", true)).toBe("•••");
    expect(displayQty("0", true)).toBe("•••");
  });
});

describe("optionLabel", () => {
  it("builds an RH-style label from the API's OCC-style fields", () => {
    expect(
      optionLabel({
        symbol: "AAPL261218C00150000",
        expiry: "2026-12-18",
        strike: "150.0000",
        right: "C",
      }),
    ).toBe("AAPL $150 C 12/18");
  });
  it("keeps fractional strikes and handles puts", () => {
    expect(
      optionLabel({
        symbol: "SPY260320P00452500",
        expiry: "2026-03-20",
        strike: "452.5000",
        right: "P",
      }),
    ).toBe("SPY $452.50 P 3/20");
  });
  it("falls back to the raw symbol when it is not OCC-shaped", () => {
    expect(
      optionLabel({
        symbol: "NVDA",
        expiry: "2026-01-16",
        strike: "120.0000",
        right: "C",
      }),
    ).toBe("NVDA $120 C 1/16");
  });
});
