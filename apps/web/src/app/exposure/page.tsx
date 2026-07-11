import ExposureChart from "@/components/ExposureChart";
import SiteHeader from "@/components/SiteHeader";
import { groupExposure, type ExposureRow } from "@/lib/exposure";
import { getViewerContext } from "@/lib/viewerContext";
import { workerFetchRaw } from "@/lib/worker";

export default async function ExposurePage() {
  const { role, masked } = await getViewerContext();

  const { status, body } = await workerFetchRaw("/internal/exposure");
  const rows = status === 200 && Array.isArray(body) ? (body as ExposureRow[]) : null;

  return (
    <>
      <SiteHeader role={role} active="/exposure" />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-6 py-10 font-sans">
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
    </>
  );
}
