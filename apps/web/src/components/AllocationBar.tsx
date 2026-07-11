import TagChips from "@/components/TagChips";

// Server component: horizontal allocation weight bars by symbol (incl. Cash).
// dataviz: single series -> one validated neutral accent (#4f8ef7, >=3:1 vs
// the dark surface), slim h-2 rounded bars on a recessive track, no legend
// (single series), text in text tokens (never series color), tabular
// figures. Label + % share one muted line above each bar. More than 10
// items collapse to top 8 + an expandable "Other" bucket (<details>, so the
// component stays server-side). Weights are always real, even for masked
// viewers.

export type AllocationItem = { label: string; weightPct: number; tags?: string[] };

const MAX_ITEMS = 10;
const TOP_N = 8;

/** Pure display grouping: >10 items -> top 8 by weight + the rest bucketed. */
export function groupForDisplay(items: AllocationItem[]): {
  top: AllocationItem[];
  rest: AllocationItem[];
} {
  if (items.length <= MAX_ITEMS) return { top: items, rest: [] };
  const sorted = [...items].sort((a, b) => b.weightPct - a.weightPct);
  return { top: sorted.slice(0, TOP_N), rest: sorted.slice(TOP_N) };
}

function BarRow({ item, max }: { item: AllocationItem; max: number }) {
  // Bar length scaled to the largest weight; shorts (negative weight)
  // render an empty track and carry the sign in the label.
  const frac = max > 0 ? Math.max(item.weightPct, 0) / max : 0;
  return (
    <div title={`${item.label}: ${item.weightPct.toFixed(2)}% of portfolio`}>
      <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-ink-2">{item.label}</span>
          <TagChips tags={item.tags} />
        </span>
        <span className="tabular-nums text-ink-2">{item.weightPct.toFixed(1)}%</span>
      </div>
      <span className="block h-2 overflow-hidden rounded-full bg-card">
        <span
          className="block h-full rounded-full bg-accent"
          style={{ width: `${(frac * 100).toFixed(2)}%` }}
        />
      </span>
    </div>
  );
}

export default function AllocationBar({ items }: { items: AllocationItem[] }) {
  const { top, rest } = groupForDisplay(items);
  // One shared scale across top AND expanded rest, so bars stay comparable.
  const max = Math.max(...items.map((i) => i.weightPct), 0);
  const otherPct = rest.reduce((sum, i) => sum + i.weightPct, 0);

  return (
    <section aria-label="Allocation by symbol">
      <h2 className="micro-label mb-3">Allocation</h2>
      <ul className="flex flex-col gap-3">
        {top.map((item) => (
          <li key={item.label}>
            <BarRow item={item} max={max} />
          </li>
        ))}
        {rest.length > 0 && (
          <li>
            <details className="group">
              <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
                  <span className="truncate text-ink-2">
                    Other ({rest.length})
                    <span className="ml-2 text-ink-3 group-open:hidden">show ▸</span>
                    <span className="ml-2 hidden text-ink-3 group-open:inline">hide ▾</span>
                  </span>
                  <span className="tabular-nums text-ink-2">{otherPct.toFixed(1)}%</span>
                </div>
                <span className="block h-2 overflow-hidden rounded-full bg-card">
                  <span
                    className="block h-full rounded-full bg-accent/50"
                    style={{
                      width: `${(max > 0 ? (Math.max(otherPct, 0) / max) * 100 : 0).toFixed(2)}%`,
                    }}
                  />
                </span>
              </summary>
              <ul className="mt-3 flex flex-col gap-3 border-l border-hairline pl-4">
                {rest.map((item) => (
                  <li key={item.label}>
                    <BarRow item={item} max={max} />
                  </li>
                ))}
              </ul>
            </details>
          </li>
        )}
      </ul>
    </section>
  );
}
