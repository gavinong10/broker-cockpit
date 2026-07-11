// Server component: horizontal allocation weight bars by symbol (incl. Cash).
// dataviz: single series -> one validated neutral accent (#4f8ef7, >=3:1 vs
// the dark surface), slim h-2 rounded bars on a recessive track, no legend
// (single series), text in text tokens (never series color), tabular
// figures. Label + % share one muted line above each bar. More than 10
// items collapse to top 8 + "Other" — pure display grouping, computed here.
// Weights are always real, even for masked viewers.

export type AllocationItem = { label: string; weightPct: number };

const MAX_ITEMS = 10;
const TOP_N = 8;

/** Pure display grouping: >10 items -> top 8 by weight + an "Other" bucket. */
function groupForDisplay(items: AllocationItem[]): AllocationItem[] {
  if (items.length <= MAX_ITEMS) return items;
  const sorted = [...items].sort((a, b) => b.weightPct - a.weightPct);
  const top = sorted.slice(0, TOP_N);
  const rest = sorted.slice(TOP_N);
  const other = rest.reduce((sum, i) => sum + i.weightPct, 0);
  return [...top, { label: `Other (${rest.length})`, weightPct: other }];
}

export default function AllocationBar({ items }: { items: AllocationItem[] }) {
  const shown = groupForDisplay(items);
  const max = Math.max(...shown.map((i) => i.weightPct), 0);

  return (
    <section aria-label="Allocation by symbol">
      <h2 className="micro-label mb-3">Allocation</h2>
      <ul className="flex flex-col gap-3">
        {shown.map((item) => {
          // Bar length scaled to the largest weight; shorts (negative
          // weight) render an empty track and carry the sign in the label.
          const frac = max > 0 ? Math.max(item.weightPct, 0) / max : 0;
          return (
            <li
              key={item.label}
              title={`${item.label}: ${item.weightPct.toFixed(2)}% of portfolio`}
            >
              <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
                <span className="truncate text-ink-2">{item.label}</span>
                <span className="tabular-nums text-ink-2">
                  {item.weightPct.toFixed(1)}%
                </span>
              </div>
              <span className="block h-2 overflow-hidden rounded-full bg-card">
                <span
                  className="block h-full rounded-full bg-accent"
                  style={{ width: `${(frac * 100).toFixed(2)}%` }}
                />
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
