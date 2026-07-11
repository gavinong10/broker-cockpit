import { describe, expect, it } from "vitest";
import { daysUntil, expiryTone, truncate } from "./baskets";

describe("daysUntil", () => {
  // Fixed "now": 2026-07-11 local time, mid-day to catch TZ sloppiness.
  const now = new Date(2026, 6, 11, 14, 30, 0);

  it("returns 0 for today", () => {
    expect(daysUntil("2026-07-11", now)).toBe(0);
  });

  it("returns 1 for tomorrow regardless of time of day", () => {
    expect(daysUntil("2026-07-12", now)).toBe(1);
    expect(daysUntil("2026-07-12", new Date(2026, 6, 11, 23, 59, 59))).toBe(1);
  });

  it("counts calendar days across month boundaries", () => {
    expect(daysUntil("2026-08-10", now)).toBe(30);
    expect(daysUntil("2026-09-18", now)).toBe(69);
  });

  it("goes negative for past dates", () => {
    expect(daysUntil("2026-07-01", now)).toBe(-10);
  });
});

describe("expiryTone", () => {
  it("is red under 10 days", () => {
    expect(expiryTone(0)).toBe("red");
    expect(expiryTone(9)).toBe("red");
  });

  it("is amber from 10 to under 30 days", () => {
    expect(expiryTone(10)).toBe("amber");
    expect(expiryTone(29)).toBe("amber");
  });

  it("is neutral at 30 days and beyond", () => {
    expect(expiryTone(30)).toBe("neutral");
    expect(expiryTone(365)).toBe("neutral");
  });

  it("treats already-expired as red", () => {
    expect(expiryTone(-3)).toBe("red");
  });
});

describe("truncate", () => {
  it("returns short strings unchanged", () => {
    expect(truncate("short thesis", 140)).toBe("short thesis");
  });

  it("returns strings exactly at the limit unchanged", () => {
    const s = "x".repeat(140);
    expect(truncate(s, 140)).toBe(s);
  });

  it("cuts long strings to at most max chars ending in an ellipsis", () => {
    const s = "a".repeat(200);
    const out = truncate(s, 140);
    expect(out.length).toBeLessThanOrEqual(140);
    expect(out.endsWith("…")).toBe(true);
  });

  it("does not leave trailing whitespace before the ellipsis", () => {
    const s = `${"a".repeat(138)} bcdefg`;
    const out = truncate(s, 140);
    expect(out).toBe(`${"a".repeat(138)}…`);
  });

  it("defaults max to 140", () => {
    expect(truncate("b".repeat(300)).length).toBeLessThanOrEqual(140);
  });
});
