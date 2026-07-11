import { describe, expect, it } from "vitest";
import { isValidEmail, normalizeEmail } from "./users";

describe("normalizeEmail", () => {
  it("lowercases and trims", () => {
    expect(normalizeEmail("  Foo.Bar@GMAIL.com ")).toBe("foo.bar@gmail.com");
  });
});

describe("isValidEmail", () => {
  it("accepts normal addresses", () => {
    expect(isValidEmail("a.b+c@example.co")).toBe(true);
    expect(isValidEmail("  UPPER@x.io ")).toBe(true);
  });
  it("rejects junk", () => {
    expect(isValidEmail("")).toBe(false);
    expect(isValidEmail("no-at-sign")).toBe(false);
    expect(isValidEmail("two@@x.com")).toBe(false);
    expect(isValidEmail("a b@x.com")).toBe(false);
    expect(isValidEmail("a@b")).toBe(false);
    expect(isValidEmail("x@y.z".padStart(340, "a"))).toBe(false);
  });
});
