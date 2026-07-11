import Link from "next/link";
import ExposureChart from "@/components/ExposureChart";
import SiteHeader from "@/components/SiteHeader";
import { display } from "@/lib/format";
import {
  filterByTag,
  groupExposure,
  themeTotals,
  type ExposureRow,
} from "@/lib/exposure";
import { getViewerContext } from "@/lib/viewerContext";
import { workerFetchRaw } from "@/lib/worker";

export default async function ExposurePage({
  searchParams,
}: {
  searchParams: Promise<{ tag?: string }>;
}) {
  const { role, masked } = await getViewerContext();
  const { tag } = await searchParams;
  const activeTag = tag || null;

  const { status, body } = await workerFetchRaw("/internal/exposure");
  const rows = status === 200 && Array.isArray(body) ? (body as ExposureRow[]) : null;
  const filtered = rows ? filterByTag(rows, activeTag) : null;
  const themes = rows ? themeTotals(rows) : [];

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
          <>
            {themes.length > 0 && (
              <section aria-label="Exposure by theme" className="flex flex-col gap-3">
                <h2 className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-2">
                  Themes{" "}
                  <span className="normal-case tracking-normal text-ink-3">
                    (overlapping — multi-tagged names count in every theme they carry)
                  </span>
                </h2>
                <div className="flex flex-wrap gap-2">
                  {themes.map((t) => {
                    const active = t.tag === activeTag;
                    return (
                      <Link
                        key={t.tag}
                        href={active ? "/exposure" : `/exposure?tag=${encodeURIComponent(t.tag)}`}
                        className={`rounded-full border px-2.5 py-1 text-[12px] tabular-nums ${
                          active
                            ? "border-accent text-accent"
                            : "border-hairline text-ink-2 hover:border-ink-3"
                        }`}
                      >
                        {t.tag}{" "}
                        <span className={active ? "text-accent" : "text-ink-3"}>
                          {display(t.total_usd.toFixed(2), masked)} · {t.count}
                        </span>
                      </Link>
                    );
                  })}
                </div>
              </section>
            )}
            {activeTag && (
              <p className="text-[13px] text-ink-2">
                Filtered to <span className="text-accent">{activeTag}</span> —{" "}
                {filtered!.length} underlying{filtered!.length === 1 ? "" : "s"}.{" "}
                <Link
                  href="/exposure"
                  className="text-ink-3 underline underline-offset-2 hover:text-ink-2"
                >
                  Clear filter
                </Link>
              </p>
            )}
            {filtered && filtered.length > 0 ? (
              <ExposureChart
                rows={groupExposure(filtered)}
                masked={masked}
                activeTag={activeTag}
              />
            ) : (
              <p className="text-sm text-ink-2">No underlyings carry this tag.</p>
            )}
          </>
        )}
        <p className="text-[12px] text-ink-3">
          Options are counted at market value (signed — short positions subtract)
          and grouped under their underlying, so a ticker&rsquo;s bar is your total
          dollar exposure to it across shares and every option line. Theme tags
          are data (underlying_tags table); options inherit their
          underlying&rsquo;s tags.
        </p>
      </main>
    </>
  );
}
