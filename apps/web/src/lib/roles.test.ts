import { describe, expect, it } from "vitest";
import { canWrite, canRead } from "./roles";

describe("role guards", () => {
  it("owner can read and write", () => {
    expect(canRead("owner")).toBe(true);
    expect(canWrite("owner")).toBe(true);
  });
  it("viewer can read, not write", () => {
    expect(canRead("viewer")).toBe(true);
    expect(canWrite("viewer")).toBe(false);
  });
  it("null role can do nothing", () => {
    expect(canRead(null)).toBe(false);
    expect(canWrite(null)).toBe(false);
  });
});
