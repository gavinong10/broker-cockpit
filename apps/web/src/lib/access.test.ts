import { describe, expect, it } from "vitest";
import { groupAccessHistory, type AccessEvent } from "./access";

const NOW = new Date("2026-07-11T20:00:00Z");

const ev = (actor: string, category: string, at: string): AccessEvent => ({
  actor,
  category,
  at,
});

describe("groupAccessHistory", () => {
  const events: AccessEvent[] = [
    ev("a@x.com", "auth.login", "2026-07-11T18:00:00Z"),
    ev("a@x.com", "auth.login", "2026-07-01T10:00:00Z"),
    ev("a@x.com", "auth.login", "2026-05-01T10:00:00Z"), // outside 30d
    ev("b@x.com", "auth.login", "2026-07-10T09:00:00Z"),
    ev("intruder@x.com", "auth.rejected", "2026-07-11T12:00:00Z"),
  ];

  it("groups per allowlisted user with last login and 30d count", () => {
    const { byUser } = groupAccessHistory(events, ["a@x.com", "b@x.com"], NOW);
    const a = byUser.get("a@x.com")!;
    expect(a.lastLoginAt).toBe("2026-07-11T18:00:00Z");
    expect(a.count30d).toBe(2);
    expect(a.recent).toHaveLength(3);
    expect(byUser.get("b@x.com")!.count30d).toBe(1);
  });

  it("users with no events get an empty record", () => {
    const { byUser } = groupAccessHistory(events, ["c@x.com"], NOW);
    const c = byUser.get("c@x.com")!;
    expect(c.lastLoginAt).toBeNull();
    expect(c.count30d).toBe(0);
    expect(c.recent).toHaveLength(0);
  });

  it("non-allowlisted actors land in the other bucket, not byUser", () => {
    // b@x.com is not allowlisted in THIS call, so its historical login joins
    // the intruder's rejected attempt in `other` (e.g. removed-user history).
    const { byUser, other } = groupAccessHistory(events, ["a@x.com"], NOW);
    expect(byUser.has("intruder@x.com")).toBe(false);
    expect(other).toHaveLength(2);
    expect(other.map((e) => e.actor)).toContain("intruder@x.com");
  });

  it("recent events are capped at 10, newest first", () => {
    const many: AccessEvent[] = Array.from({ length: 15 }, (_, i) =>
      ev("a@x.com", "auth.login", `2026-07-${String(i + 1).padStart(2, "0")}T00:00:00Z`),
    );
    const { byUser } = groupAccessHistory(many, ["a@x.com"], NOW);
    const a = byUser.get("a@x.com")!;
    expect(a.recent).toHaveLength(10);
    expect(a.recent[0].at > a.recent[9].at).toBe(true);
  });
});
