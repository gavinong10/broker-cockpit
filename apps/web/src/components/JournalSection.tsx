"use client";

// Owner-only journal thread + add-entry form. Rendered ONLY when the
// effective view is owner (viewers and owner-in-preview get a private
// placeholder from the parent, and never receive entry data).

import { useActionState } from "react";
import Link from "next/link";
import {
  addJournalEntry,
  deleteJournalEntry,
  type JournalFormState,
} from "@/app/actions/journal";

export type JournalEntry = {
  id: number;
  symbol: string;
  at: string;
  tag: string;
  note: string;
  target_usd: string | null;
  stop_usd: string | null;
  confidence: number | null;
  source_ref: string | null;
};

const TAG_SUGGESTIONS = ["thesis", "add", "trim", "roll", "hedge", "iv-crush",
  "earnings-play", "dca", "exit", "autopsy"];

const AT_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago",
  month: "short", day: "numeric", year: "numeric",
  hour: "numeric", minute: "2-digit", timeZoneName: "short",
});

const IDLE: JournalFormState = { ok: false, error: null };

function DeleteButton({ entry }: { entry: JournalEntry }) {
  const [state, action, pending] = useActionState(deleteJournalEntry, IDLE);
  return (
    <form action={action} className="inline">
      <input type="hidden" name="id" value={entry.id} />
      <input type="hidden" name="symbol" value={entry.symbol} />
      <button
        type="submit"
        disabled={pending}
        aria-label={`Delete entry ${entry.id}`}
        className="text-xs text-ink-3 hover:text-loss"
      >
        {pending ? "…" : "delete"}
      </button>
      {state.error && <span className="ml-2 text-xs text-loss">{state.error}</span>}
    </form>
  );
}

export default function JournalSection({
  symbol,
  entries,
  showSymbol = false,
}: {
  symbol?: string;
  entries: JournalEntry[];
  showSymbol?: boolean;
}) {
  const [state, action, pending] = useActionState(addJournalEntry, IDLE);

  return (
    <section aria-label="Journal" className="flex flex-col gap-4">
      <h2 className="micro-label">Journal</h2>

      {symbol && (
        <form
          action={action}
          className="flex flex-col gap-3 rounded-xl border border-hairline bg-card p-5"
        >
          <input type="hidden" name="symbol" value={symbol} />
          <div className="flex flex-wrap gap-3">
            <input
              name="tag"
              list="journal-tags"
              required
              placeholder="tag (e.g. thesis)"
              className="w-40 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-ink-3"
            />
            <datalist id="journal-tags">
              {TAG_SUGGESTIONS.map((t) => <option key={t} value={t} />)}
            </datalist>
            <input name="target_usd" inputMode="decimal" placeholder="target $"
              className="w-28 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-ink-3" />
            <input name="stop_usd" inputMode="decimal" placeholder="stop $"
              className="w-28 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-ink-3" />
            <select name="confidence" defaultValue=""
              className="w-36 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink">
              <option value="">confidence —</option>
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>confidence {n}/5</option>
              ))}
            </select>
          </div>
          <textarea
            name="note"
            required
            rows={3}
            placeholder="Why? The reasoning you'll want to reread in a year."
            className="rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3"
          />
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={pending}
              className="rounded-md border border-hairline bg-surface px-4 py-1.5 text-sm text-ink hover:bg-hover"
            >
              {pending ? "Saving…" : "Add entry"}
            </button>
            {state.error && <span className="text-sm text-loss">{state.error}</span>}
            {state.ok && !pending && <span className="text-sm text-gain">Saved.</span>}
          </div>
        </form>
      )}

      {entries.length === 0 ? (
        <p className="text-sm text-ink-3">No entries yet.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {entries.map((e) => (
            <li key={e.id} className="rounded-xl border border-hairline bg-card p-4">
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
                {showSymbol && (
                  <Link
                    href={`/positions/${encodeURIComponent(e.symbol)}`}
                    className="font-medium text-accent hover:underline"
                  >
                    {e.symbol}
                  </Link>
                )}
                <span className="rounded-full border border-hairline px-2 py-0.5">{e.tag}</span>
                <time dateTime={e.at}>{AT_FMT.format(new Date(e.at))}</time>
                {e.confidence !== null && <span>confidence {e.confidence}/5</span>}
                {e.target_usd !== null && <span>target ${e.target_usd}</span>}
                {e.stop_usd !== null && <span>stop ${e.stop_usd}</span>}
                <span className="ml-auto"><DeleteButton entry={e} /></span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{e.note}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
