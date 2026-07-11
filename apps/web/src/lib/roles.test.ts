import { describe, expect, it } from "vitest";
import {
  canManageUsers,
  canOperateFactory,
  canRead,
  canWrite,
  effectiveView,
  isMasked,
  PERMISSION_DENIED_MESSAGE,
} from "./roles";

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
  it("factory and user-admin actions are owner-only", () => {
    expect(canOperateFactory("owner")).toBe(true);
    expect(canOperateFactory("viewer")).toBe(false);
    expect(canOperateFactory(null)).toBe(false);
    expect(canManageUsers("owner")).toBe(true);
    expect(canManageUsers("viewer")).toBe(false);
    expect(canManageUsers(null)).toBe(false);
  });
  it("the shared permission error names the owner-only rule", () => {
    expect(PERMISSION_DENIED_MESSAGE).toBe("You don't have permission — owner only.");
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
