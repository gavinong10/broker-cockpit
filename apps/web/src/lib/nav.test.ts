import { describe, expect, it } from "vitest";
import { navTabsFor } from "./nav";

// The mobile drawer (MobileNav) and the desktop bar are both fed the array
// returned by navTabsFor — so proving the gate here proves it in BOTH surfaces.
describe("navTabsFor — owner-only Users gate survives", () => {
  const labels = (role: Parameters<typeof navTabsFor>[0]) =>
    navTabsFor(role).map((t) => t.label);

  it("owner sees the Users tab", () => {
    expect(labels("owner")).toContain("Users");
    expect(navTabsFor("owner").some((t) => t.href === "/admin")).toBe(true);
  });

  it("viewer does NOT see the Users tab", () => {
    expect(labels("viewer")).not.toContain("Users");
    expect(navTabsFor("viewer").some((t) => t.href === "/admin")).toBe(false);
  });

  it("null (owner-in-viewer-preview / no role) does NOT see the Users tab", () => {
    expect(labels(null)).not.toContain("Users");
  });

  it("all roles share the same read-only tabs", () => {
    const readonly = ["Portfolio", "Exposure", "Journal", "Capabilities", "Features"];
    expect(labels("owner")).toEqual([...readonly, "Users"]);
    expect(labels("viewer")).toEqual(readonly);
    expect(labels(null)).toEqual(readonly);
  });
});
