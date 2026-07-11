import SiteHeader from "@/components/SiteHeader";
import JournalSection, { type JournalEntry } from "@/components/JournalSection";
import { canRead } from "@/lib/roles";
import { getViewerContext } from "@/lib/viewerContext";
import { workerFetchRaw } from "@/lib/worker";

export const dynamic = "force-dynamic";

// Readable by every signed-in role (entries + search — the owner accepted
// that notes/targets/stops are shared free text, not maskable dollars).
// Mutations (add/delete on the position pages) stay owner-only via the
// server actions in actions/journal.ts.
export default async function JournalPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; tag?: string; symbol?: string }>;
}) {
  const [{ q = "", tag = "", symbol = "" }, view] = await Promise.all([
    searchParams,
    getViewerContext(),
  ]);

  // Sessions whose role was revoked (null) read nothing — journal text is
  // unmaskable, so it is only for current owner/viewer roles.
  if (!canRead(view.role)) {
    return (
      <main className="mx-auto w-full max-w-5xl px-6 py-10 font-sans">
        <SiteHeader active="/journal" />
        <p className="mt-8 text-sm text-ink-3">Not available.</p>
      </main>
    );
  }

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (tag) params.set("tag", tag);
  if (symbol) params.set("symbol", symbol.toUpperCase());
  params.set("limit", "100");
  const res = await workerFetchRaw(`/internal/journal?${params.toString()}`);
  const entries = res.status === 200 ? (res.body as JournalEntry[]) : [];

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-10 font-sans">
      <SiteHeader active="/journal" />

      <form method="GET" className="flex flex-wrap items-center gap-3">
        <input
          name="q"
          defaultValue={q}
          placeholder="Search notes…"
          className="w-64 rounded-md border border-hairline bg-card px-3 py-1.5 text-sm text-ink placeholder:text-ink-3"
        />
        <input
          name="symbol"
          defaultValue={symbol}
          placeholder="symbol"
          className="w-28 rounded-md border border-hairline bg-card px-3 py-1.5 text-sm text-ink placeholder:text-ink-3"
        />
        <input
          name="tag"
          defaultValue={tag}
          placeholder="tag"
          className="w-28 rounded-md border border-hairline bg-card px-3 py-1.5 text-sm text-ink placeholder:text-ink-3"
        />
        <button
          type="submit"
          className="rounded-md border border-hairline bg-card px-4 py-1.5 text-sm text-ink hover:bg-hover"
        >
          Search
        </button>
        {(q || tag || symbol) && (
          <a href="/journal" className="text-sm text-ink-2 hover:text-ink">
            clear
          </a>
        )}
      </form>

      <JournalSection entries={entries} showSymbol />
      <p className="text-xs text-ink-3">
        To add an entry, open the position&apos;s page and write it there —
        entries anchor to a symbol.
      </p>
    </main>
  );
}
