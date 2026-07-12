import { describe, expect, it } from "vitest";
import { firstObservedIndex } from "./performance";

describe("firstObservedIndex", () => {
  it("returns 0 when nothing is estimated", () => {
    expect(
      firstObservedIndex([{ estimated: false }, { estimated: false }]),
    ).toBe(0);
  });

  it("returns the boundary index for a reconstructed tail", () => {
    expect(
      firstObservedIndex([
        { estimated: true },
        { estimated: true },
        { estimated: false },
      ]),
    ).toBe(2);
  });

  it("returns series.length when every point is estimated", () => {
    expect(
      firstObservedIndex([{ estimated: true }, { estimated: true }]),
    ).toBe(2);
  });

  it("handles the empty series", () => {
    expect(firstObservedIndex([])).toBe(0);
  });
});
