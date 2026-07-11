import { describe, expect, it } from "vitest";
import { canWrite, canRead, isMasked, effectiveView } from "./roles";

describe("isMasked — dollars are owner-only", () => {
  it("owner unmasked by default, masked only via own flag", () => {
    expect(isMasked("owner", false)).toBe(false);
    expect(isMasked("owner", undefined)).toBe(false);
    expect(isMasked("owner", true)).toBe(true);
  });
  it("viewer always masked, flag cannot unmask", () => {
    expect(isMasked("viewer", false)).toBe(true);
    expect(isMasked("viewer", true)).toBe(true);
    expect(isMasked("viewer", undefined)).toBe(true);
  });
  it("null role always masked", () => {
    expect(isMasked(null, false)).toBe(true);
  });
});

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

describe("effectiveView — owner viewer-preview is a strict downgrade", () => {
  it("owner + preview cookie renders as masked viewer", () => {
    expect(effectiveView("owner", false, true)).toEqual({
      role: "viewer",
      masked: true,
      previewing: true,
    });
  });
  it("owner without cookie is untouched", () => {
    expect(effectiveView("owner", false, false)).toEqual({
      role: "owner",
      masked: false,
      previewing: false,
    });
  });
  it("viewer with a stray cookie cannot elevate or change", () => {
    expect(effectiveView("viewer", true, true)).toEqual({
      role: "viewer",
      masked: true,
      previewing: false,
    });
  });
  it("null role with a stray cookie stays null and masked", () => {
    expect(effectiveView(null, undefined, true)).toEqual({
      role: null,
      masked: true,
      previewing: false,
    });
  });
});
