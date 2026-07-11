// Server component: horizontal allocation weight bars by symbol (incl. Cash).
// dataviz: single series -> one validated hue (slot-1 blue, light #2a78d6 /
// dark #3987e5), thin bars, 4px rounded data-end anchored to a square left
// baseline, no legend (single series), text in text tokens (never series
// color), tabular figures on the value column. Weights are always real, even
// for masked viewers.

export type AllocationItem = { label: string; weightPct: number };

export default function AllocationBar({ items }: { items: AllocationItem[] }) {
  const max = Math.max(...items.map((i) => i.weightPct), 0);

  return (
    <section aria-label="Allocation by symbol">
      <h2 className="mb-2 text-sm font-medium text-zinc-500 dark:text-zinc-400">
        Allocation
      </h2>
      <ul className="flex flex-col gap-1.5">
        {items.map((item) => {
          // Bar length scaled to the largest weight; shorts (negative
          // weight) render an empty track and carry the sign in the label.
          const frac = max > 0 ? Math.max(item.weightPct, 0) / max : 0;
          return (
            <li
              key={item.label}
              className="grid grid-cols-[7rem_1fr_3.5rem] items-center gap-3"
              title={`${item.label}: ${item.weightPct.toFixed(2)}% of portfolio`}
            >
              <span className="truncate text-sm text-zinc-950 dark:text-zinc-50">
                {item.label}
              </span>
              <span className="h-2.5 overflow-hidden rounded-r bg-zinc-200/60 dark:bg-zinc-800">
                <span
                  className="block h-full rounded-r bg-[#2a78d6] dark:bg-[#3987e5]"
                  style={{ width: `${(frac * 100).toFixed(2)}%` }}
                />
              </span>
              <span className="text-right text-sm tabular-nums text-zinc-500 dark:text-zinc-400">
                {item.weightPct.toFixed(1)}%
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
