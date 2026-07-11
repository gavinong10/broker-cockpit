import { auth } from "@/auth";
import ExposureChart from "@/components/ExposureChart";
import NavTabs from "@/components/NavTabs";
import { groupExposure, type ExposureRow } from "@/lib/exposure";
import { isMasked } from "@/lib/roles";
import { workerFetchRaw } from "@/lib/worker";

export default async function ExposurePage() {
  const session = await auth();
  const u = session?.user as
    | { role?: "owner" | "viewer" | null; mask_amounts?: boolean }
    | undefined;
  const role = u?.role ?? null;
  const masked = isMasked(role, u?.mask_amounts);

  const { status, body } = await workerFetchRaw("/internal/exposure");
  const rows = status === 200 && Array.isArray(body) ? (body as ExposureRow[]) : null;

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-6 py-10">
      <NavTabs role={role} active="/exposure" />
      {rows === null ? (
        <p className="text-sm text-ink-2">
          Exposure data unavailable (worker returned {status}).
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-2">No positions yet.</p>
      ) : (
        <ExposureChart rows={groupExposure(rows)} masked={masked} />
      )}
      <p className="text-[12px] text-ink-3">
        Options are counted at market value (signed — short positions subtract)
        and grouped under their underlying, so a ticker&rsquo;s bar is your total
        dollar exposure to it across shares and every option line.
      </p>
    </main>
  );
}
