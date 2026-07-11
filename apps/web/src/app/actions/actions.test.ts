// Server actions must refuse non-owner sessions BEFORE any worker call —
// the web tier holds the internal token regardless of user role, so this
// re-check is the enforcement point. These tests mock the session and assert
// (a) the clean permission error comes back, (b) the worker is NEVER called,
// (c) the refusal is audit-logged as perm.denied.

import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  auth: vi.fn(),
  auditFromWeb: vi.fn(async () => {}),
  workerPost: vi.fn(),
  workerDelete: vi.fn(),
  insertViewer: vi.fn(),
  deleteViewer: vi.fn(),
  revalidatePath: vi.fn(),
}));

vi.mock("@/auth", () => ({ auth: mocks.auth }));
vi.mock("@/db", () => ({
  auditFromWeb: mocks.auditFromWeb,
  insertViewer: mocks.insertViewer,
  deleteViewer: mocks.deleteViewer,
}));
vi.mock("@/lib/worker", () => ({
  workerPost: mocks.workerPost,
  workerDelete: mocks.workerDelete,
}));
vi.mock("next/cache", () => ({ revalidatePath: mocks.revalidatePath }));

import { PERMISSION_DENIED_MESSAGE } from "@/lib/roles";
import { createFeature, factoryPause, featureAction } from "./features";
import { addJournalEntry, deleteJournalEntry } from "./journal";
import { refreshRobinhood } from "./rh-refresh";
import { addViewer, removeViewer } from "./users";

function sessionFor(role: "owner" | "viewer" | null) {
  return { user: { email: role ? `${role}@example.com` : undefined, role } };
}

function form(fields: Record<string, string>): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) fd.set(k, v);
  return fd;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("owner-only server actions refuse non-owners before touching the worker", () => {
  const cases: {
    name: string;
    run: () => Promise<{ message?: string; error?: string | null } | { kind: string; message?: string }>;
  }[] = [
    {
      name: "createFeature",
      run: () =>
        createFeature({ ok: true, message: "" }, form({ prompt: "add a long enough feature prompt here" })),
    },
    { name: "factoryPause", run: () => factoryPause(true) },
    { name: "featureAction accept", run: () => featureAction("slug", "accept") },
    { name: "featureAction revert", run: () => featureAction("slug", "revert") },
    { name: "featureAction kill", run: () => featureAction("slug", "kill") },
    {
      name: "addJournalEntry",
      run: () =>
        addJournalEntry({ ok: false, error: null }, form({ symbol: "SPY", tag: "thesis", note: "n" })),
    },
    {
      name: "deleteJournalEntry",
      run: () => deleteJournalEntry({ ok: false, error: null }, form({ id: "1", symbol: "SPY" })),
    },
    {
      name: "refreshRobinhood",
      run: () => refreshRobinhood({ kind: "idle" }, form({ username: "u", password: "p" })),
    },
  ];

  for (const role of ["viewer", null] as const) {
    for (const c of cases) {
      it(`${c.name} refuses role=${String(role)} with the clean error`, async () => {
        mocks.auth.mockResolvedValue(sessionFor(role));
        const res = (await c.run()) as { message?: string; error?: string | null };
        const msg = "message" in res && res.message ? res.message : res.error;
        expect(msg).toBe(PERMISSION_DENIED_MESSAGE);
        expect(mocks.workerPost).not.toHaveBeenCalled();
        expect(mocks.workerDelete).not.toHaveBeenCalled();
        expect(mocks.auditFromWeb).toHaveBeenCalledWith(
          role ? "viewer@example.com" : "unknown",
          "perm.denied",
          expect.objectContaining({ action: expect.any(String) }),
        );
      });
    }
  }

  it("admin actions refuse viewers without touching the DB mutators", async () => {
    mocks.auth.mockResolvedValue(sessionFor("viewer"));
    const add = await addViewer({ ok: null, error: null }, form({ email: "x@y.com" }));
    const rm = await removeViewer({ ok: null, error: null }, form({ email: "x@y.com" }));
    expect(add.error).toBe(PERMISSION_DENIED_MESSAGE);
    expect(rm.error).toBe(PERMISSION_DENIED_MESSAGE);
    expect(mocks.insertViewer).not.toHaveBeenCalled();
    expect(mocks.deleteViewer).not.toHaveBeenCalled();
  });
});

describe("owner sessions pass the gate and reach the worker", () => {
  it("createFeature calls the worker for an owner", async () => {
    mocks.auth.mockResolvedValue(sessionFor("owner"));
    mocks.workerPost.mockResolvedValue({ status: 202, body: { slug: "s" } });
    const res = await createFeature(
      { ok: true, message: "" },
      form({ prompt: "add a long enough feature prompt here" }),
    );
    expect(res.ok).toBe(true);
    expect(mocks.workerPost).toHaveBeenCalledOnce();
    expect(mocks.auditFromWeb).not.toHaveBeenCalledWith(
      expect.anything(),
      "perm.denied",
      expect.anything(),
    );
  });

  it("addJournalEntry calls the worker for an owner", async () => {
    mocks.auth.mockResolvedValue(sessionFor("owner"));
    mocks.workerPost.mockResolvedValue({ status: 200, body: {} });
    const res = await addJournalEntry(
      { ok: false, error: null },
      form({ symbol: "SPY", tag: "thesis", note: "note" }),
    );
    expect(res.ok).toBe(true);
    expect(mocks.workerPost).toHaveBeenCalledOnce();
  });
});
