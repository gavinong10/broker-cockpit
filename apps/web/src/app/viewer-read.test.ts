// Viewer-session read access: with the effective view mocked to a plain
// viewer, the Features / Journal / Capabilities pages must return their
// CONTENT trees (list + forms render; mutations are refused server-side by
// the actions, covered in actions/actions.test.ts), while /admin still
// returns its owner-only wall. Pages are async server components — we invoke
// them directly and walk the returned element tree.

import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getViewerContext: vi.fn(),
  workerFetchRaw: vi.fn(),
}));

vi.mock("@/lib/viewerContext", () => ({
  VIEWER_PREVIEW_COOKIE: "viewer-preview",
  getViewerContext: mocks.getViewerContext,
}));
vi.mock("@/lib/worker", () => ({ workerFetchRaw: mocks.workerFetchRaw }));
vi.mock("@/db", () => ({
  listUsers: vi.fn(async () => []),
  listAccessEvents: vi.fn(async () => []),
  auditFromWeb: vi.fn(async () => {}),
}));
vi.mock("@/auth", () => ({ auth: vi.fn(async () => null) }));

import FeaturesPage from "./features/page";
import JournalPage from "./journal/page";
import CapabilitiesPage from "./capabilities/page";
import AdminPage from "./admin/page";
import FeatureFactory from "@/components/FeatureFactory";
import JournalSection from "@/components/JournalSection";
import UserAdmin from "@/components/UserAdmin";

type AnyElement = {
  type?: unknown;
  props?: { children?: unknown; [k: string]: unknown };
};

/** Depth-first search of a React element tree for a node matching `pred`.
 * Only walks already-created elements (nested server components stay
 * unevaluated, which is enough: we assert on which components the page
 * decided to render and with what props). */
function findNode(
  node: unknown,
  pred: (el: AnyElement) => boolean,
): AnyElement | null {
  if (node === null || node === undefined || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findNode(child, pred);
      if (hit) return hit;
    }
    return null;
  }
  const el = node as AnyElement;
  if (pred(el)) return el;
  return findNode(el.props?.children, pred);
}

function textIncludes(node: unknown, needle: string): boolean {
  if (typeof node === "string") return node.includes(needle);
  if (node === null || node === undefined || typeof node !== "object") return false;
  if (Array.isArray(node)) return node.some((c) => textIncludes(c, needle));
  return textIncludes((node as AnyElement).props?.children, needle);
}

const VIEWER = {
  role: "viewer" as const,
  realRole: "viewer" as const,
  masked: true,
  previewing: false,
  email: "viewer@example.com",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getViewerContext.mockResolvedValue(VIEWER);
  mocks.workerFetchRaw.mockImplementation(async (path: string) => {
    if (path.startsWith("/internal/features/runner")) {
      return { status: 200, body: { configured: true, paused: false } };
    }
    if (path.startsWith("/internal/features")) {
      return {
        status: 200,
        body: [
          {
            slug: "demo-feature",
            prompt: "demo prompt",
            model: "fable",
            status: "built",
            diff_stat: "1 file changed",
            risky_paths: null,
            merge_sha: null,
            report: null,
            created_at: "2026-07-11T00:00:00Z",
            updated_at: "2026-07-11T00:00:00Z",
          },
        ],
      };
    }
    if (path.startsWith("/internal/journal")) {
      return {
        status: 200,
        body: [
          {
            id: 1,
            symbol: "SPY",
            at: "2026-07-11T00:00:00Z",
            tag: "thesis",
            note: "shared note",
            target_usd: null,
            stop_usd: null,
            confidence: null,
            source_ref: null,
          },
        ],
      };
    }
    return { status: 200, body: [] };
  });
});

describe("viewer sessions read the formerly owner-only pages", () => {
  it("features page renders FeatureFactory with the feature list", async () => {
    const tree = await FeaturesPage();
    const factory = findNode(tree, (el) => el.type === FeatureFactory);
    expect(factory).not.toBeNull();
    expect(
      (factory!.props as { initialFeatures: { slug: string }[] }).initialFeatures,
    ).toHaveLength(1);
  });

  it("journal page renders JournalSection with entries", async () => {
    const tree = await JournalPage({ searchParams: Promise.resolve({}) });
    const section = findNode(tree, (el) => el.type === JournalSection);
    expect(section).not.toBeNull();
    expect(
      (section!.props as { entries: { note: string }[] }).entries[0]?.note,
    ).toBe("shared note");
  });

  it("capabilities page renders doc sections, not a wall", async () => {
    const tree = await CapabilitiesPage();
    expect(textIncludes(tree, "owner-only")).toBe(false);
    // Repo docs/capabilities has real *.md files; the page renders a
    // <section> per doc (or the explicit empty-state copy, never a wall).
    const section = findNode(tree, (el) => el.type === "section");
    const emptyState = textIncludes(tree, "No capability docs found");
    expect(section !== null || emptyState).toBe(true);
  });

  it("admin page still walls viewers — no UserAdmin, no user data", async () => {
    const tree = await AdminPage();
    expect(findNode(tree, (el) => el.type === UserAdmin)).toBeNull();
    expect(textIncludes(tree, "owner-only")).toBe(true);
  });
});
