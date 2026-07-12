import SiteHeader from "@/components/SiteHeader";
import { canRead } from "@/lib/roles";
import { getViewerContext } from "@/lib/viewerContext";
import { workerFetchRaw } from "@/lib/worker";
import FeatureFactory, { type Feature } from "@/components/FeatureFactory";

export const dynamic = "force-dynamic";

// Readable by every signed-in role (list, statuses, prompts, diffs). The
// write controls render for viewers too and submit — each server action in
// actions/features.ts re-verifies the REAL owner role and returns the clean
// permission error, so rendering here is UX, not the gate. Uses the
// effective view so owner-in-preview sees exactly the viewer experience.
export default async function FeaturesPage() {
  const { role } = await getViewerContext();

  // Revoked (null-role) sessions read nothing — prompts/diffs are internal.
  if (!canRead(role)) {
    return (
      <div className="min-h-screen">
        <SiteHeader active="/features" />
        <main className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
          <p className="text-sm text-ink-2">Not available.</p>
        </main>
      </div>
    );
  }

  const [listRes, runnerRes] = await Promise.all([
    workerFetchRaw("/internal/features"),
    workerFetchRaw("/internal/features/runner"),
  ]);
  const features: Feature[] =
    listRes.status === 200 && Array.isArray(listRes.body) ? (listRes.body as Feature[]) : [];
  const runnerBody = runnerRes.body as { configured?: boolean; paused?: boolean } | undefined;
  const runnerConfigured = runnerRes.status === 200 && runnerBody?.configured === true;
  const runnerPaused = runnerRes.status === 200 && runnerBody?.paused === true;

  return (
    <div className="min-h-screen">
      <SiteHeader active="/features" />
      <main className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-ink">Features</h1>
          <p className="mt-1 max-w-2xl text-[13px] text-ink-2">
            Describe a change; Claude builds it in an isolated git worktree — never
            on the live branch. Preview the diff, then Accept (merge &amp; redeploy) or
            Revert. Builds are sandboxed to code changes only: no database, no external
            services, no secrets.
          </p>
        </div>
        <FeatureFactory
          initialFeatures={features}
          runnerConfigured={runnerConfigured}
          runnerPaused={runnerPaused}
        />
      </main>
    </div>
  );
}
