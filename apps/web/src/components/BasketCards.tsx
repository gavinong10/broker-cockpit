// Server component: basket ("campaign") cards on the dashboard. Each card
// links to the basket's mini-portfolio page. Dollars (deployed / current /
// P/L $) respect masking; P/L % is always real. The nearest-expiry runway
// chip escalates amber < 30d, red < 10d.

import Link from "next/link";
import { daysUntil, expiryTone, truncate, type ExpiryTone } from "@/lib/baskets";
import { display, pct, usd } from "@/lib/format";
import type { Basket } from "@/lib/portfolio";

const CHIP_TONE: Record<ExpiryTone, string> = {
  red: "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-300",
  amber: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  neutral: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
};

function ExpiryChip({ isoDate }: { isoDate: string }) {
  const days = daysUntil(isoDate);
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] tabular-nums ${CHIP_TONE[expiryTone(days)]}`}
      title={`Nearest option expiry ${isoDate}`}
    >
      {days}d to nearest expiry
    </span>
  );
}

export default function BasketCards({
  baskets,
  masked,
}: {
  baskets: Basket[];
  masked: boolean;
}) {
  return (
    <section aria-label="Baskets">
      <h2 className="mb-2 text-sm font-medium text-zinc-500 dark:text-zinc-400">
        Baskets
      </h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {baskets.map((b) => {
          const pl = Number(b.pl_usd);
          const plPct = Number(b.pl_pct);
          return (
            <li key={b.slug}>
              <Link
                href={`/baskets/${encodeURIComponent(b.slug)}`}
                className="flex h-full flex-col gap-2 rounded-md border border-zinc-200 px-4 py-3 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900/50"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-zinc-950 dark:text-zinc-50">
                    {b.name}
                  </span>
                  <span className="flex items-center gap-1.5">
                    {b.status !== "open" && (
                      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] uppercase text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                        {b.status}
                      </span>
                    )}
                    {b.nearest_expiry !== null && (
                      <ExpiryChip isoDate={b.nearest_expiry} />
                    )}
                  </span>
                </div>
                <p
                  className="text-sm text-zinc-500 dark:text-zinc-400"
                  title={b.thesis}
                >
                  {truncate(b.thesis)}
                </p>
                <p className="mt-auto flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm tabular-nums">
                  <span className="text-zinc-950 dark:text-zinc-50">
                    {display(b.deployed_usd, masked)}
                    <span className="text-zinc-400 dark:text-zinc-500"> → </span>
                    {display(b.current_value_usd, masked)}
                  </span>
                  <span
                    className={
                      pl >= 0
                        ? "text-[#006300] dark:text-[#0ca30c]"
                        : "text-[#d03b3b]"
                    }
                  >
                    {masked ? "•••" : `${pl >= 0 ? "+" : ""}${usd(b.pl_usd)}`}
                    {" "}({plPct >= 0 ? "+" : ""}
                    {pct(b.pl_pct)})
                  </span>
                </p>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
