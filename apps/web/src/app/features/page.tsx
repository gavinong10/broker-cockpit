import { redirect } from "next/navigation";
import { auth } from "@/auth";
import SiteHeader from "@/components/SiteHeader";
import { workerFetchRaw } from "@/lib/worker";
import FeatureFactory, { type Feature } from "@/components/FeatureFactory";

export const dynamic = "force-dynamic";

export default async function FeaturesPage() {
  const session = await auth();
  const role = (session?.user as { role?: "owner" | "viewer" | null } | undefined)?.role ?? null;
  if (role !== "owner") redirect("/");

  const [listRes, runnerRes] = await Promise.all([
    workerFetchRaw("/internal/features"),
    workerFetchRaw("/internal/features/runner"),
  ]);
  const features: Feature[] =
    listRes.status === 200 && Array.isArray(listRes.body) ? (listRes.body as Feature[]) : [];
  const runnerConfigured =
    runnerRes.status === 200 && (runnerRes.body as { configured?: boolean })?.configured === true;

  return (
    <div className="min-h-screen">
      <SiteHeader role={role} active="/features" />
      <main className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-ink">Features</h1>
          <p className="mt-1 max-w-2xl text-[13px] text-ink-2">
            Describe a change; Claude builds it in an isolated git worktree — never
            on the live branch. Preview the diff, then Accept (merge &amp; redeploy) or
            Revert. Builds are sandboxed to code changes only: no database, no external
            services, no secrets.
          </p>
        </div>
        <FeatureFactory initialFeatures={features} runnerConfigured={runnerConfigured} />
      </main>
    </div>
  );
}
