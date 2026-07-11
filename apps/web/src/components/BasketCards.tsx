// Server component: basket ("campaign") cards on the dashboard. Each card
// links to the basket's mini-portfolio page. Dollars (deployed / current /
// P/L $) respect masking; P/L % is always real. The nearest-expiry runway
// chip escalates amber < 30d, red < 10d (tone + the day count itself, so
// urgency is never color-alone).

import Link from "next/link";
import { daysUntil, expiryTone, truncate, type ExpiryTone } from "@/lib/baskets";
import { display, pct, usd } from "@/lib/format";
import type { Basket } from "@/lib/portfolio";

// Tiny rounded outline chips; tone keeps its semantics via border + text.
const CHIP_TONE: Record<ExpiryTone, string> = {
  red: "border-loss/50 text-loss",
  amber: "border-amber-400/50 text-amber-400",
  neutral: "border-hairline text-ink-2",
};

function ExpiryChip({ isoDate }: { isoDate: string }) {
  const days = daysUntil(isoDate);
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] tabular-nums ${CHIP_TONE[expiryTone(days)]}`}
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
      <h2 className="micro-label mb-3">Baskets</h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {baskets.map((b) => {
          const pl = Number(b.pl_usd);
          const plPct = Number(b.pl_pct);
          return (
            <li key={b.slug}>
              <Link
                href={`/baskets/${encodeURIComponent(b.slug)}`}
                className="flex h-full flex-col gap-2 rounded-xl border border-hairline bg-card p-5 transition-colors hover:bg-hover"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-ink">
                    {b.name}
                  </span>
                  <span className="flex items-center gap-1.5">
                    {b.status !== "open" && (
                      <span className="rounded-full border border-hairline px-2 py-0.5 text-[11px] uppercase tracking-[0.08em] text-ink-2">
                        {b.status}
                      </span>
                    )}
                    {b.nearest_expiry !== null && (
                      <ExpiryChip isoDate={b.nearest_expiry} />
                    )}
                  </span>
                </div>
                <p className="text-sm leading-5 text-ink-2" title={b.thesis}>
                  {truncate(b.thesis)}
                </p>
                <p className="mt-auto flex flex-wrap items-baseline gap-x-3 gap-y-1 pt-1 text-sm tabular-nums">
                  <span className="text-ink">
                    {display(b.deployed_usd, masked)}
                    <span className="text-ink-3"> → </span>
                    {display(b.current_value_usd, masked)}
                  </span>
                  <span className={pl >= 0 ? "text-gain" : "text-loss"}>
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
