// Server component: positions table in API order (market value desc).
// Each row is a <details> element (stays server-side) whose summary is the
// data row; expanding reveals the per-broker quantity breakdown. The
// symbol/label cell links to the position detail page. Dollar cells respect
// masking; qty stays real (not a dollar amount).

import Link from "next/link";
import { display, usd } from "@/lib/format";
import { positionLabel, type PortfolioPosition } from "@/lib/portfolio";

const GRID =
  "grid grid-cols-[minmax(9rem,2fr)_repeat(5,minmax(5.5rem,1fr))] items-center gap-2";

function DayChange({ value, masked }: { value: string; masked: boolean }) {
  const n = Number(value);
  const color =
    n >= 0 ? "text-[#006300] dark:text-[#0ca30c]" : "text-[#d03b3b]";
  return (
    <span className={`text-right text-sm tabular-nums ${color}`}>
      {masked ? "•••" : `${n >= 0 ? "+" : ""}${usd(value)}`}
    </span>
  );
}

export default function PositionTable({
  positions,
  masked,
}: {
  positions: PortfolioPosition[];
  masked: boolean;
}) {
  if (positions.length === 0) {
    return (
      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        No positions yet — they appear after the first broker sync.
      </p>
    );
  }

  return (
    <section aria-label="Positions">
      <h2 className="mb-2 text-sm font-medium text-zinc-500 dark:text-zinc-400">
        Positions
      </h2>
      <div className={`${GRID} border-b border-zinc-200 pb-1 dark:border-zinc-800`}>
        {["Symbol", "Qty", "Last price", "Day change", "Market value", "Unrealized P/L"].map(
          (h, i) => (
            <span
              key={h}
              className={`text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400 ${
                i === 0 ? "" : "text-right"
              }`}
            >
              {h}
            </span>
          ),
        )}
      </div>
      <ul>
        {positions.map((p) => {
          const label = positionLabel(p);
          const pl = Number(p.unrealized_pl_usd);
          return (
            <li key={`${p.symbol}-${p.sec_type}`}>
              <details className="group border-b border-zinc-100 dark:border-zinc-900">
                <summary
                  className={`${GRID} cursor-pointer list-none py-2 hover:bg-zinc-50 dark:hover:bg-zinc-900/50`}
                >
                  <span>
                    <Link
                      href={`/positions/${encodeURIComponent(p.symbol)}`}
                      className="text-sm font-medium text-zinc-950 underline-offset-2 hover:underline dark:text-zinc-50"
                    >
                      {label}
                    </Link>
                    {p.sec_type === "OPT" && (
                      <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] uppercase text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                        option
                      </span>
                    )}
                  </span>
                  <span className="text-right text-sm tabular-nums text-zinc-950 dark:text-zinc-50">
                    {p.qty}
                  </span>
                  <span className="text-right text-sm tabular-nums text-zinc-950 dark:text-zinc-50">
                    {p.last_price_usd === null ? "—" : display(p.last_price_usd, masked)}
                  </span>
                  <DayChange value={p.day_change_usd} masked={masked} />
                  <span className="text-right text-sm tabular-nums text-zinc-950 dark:text-zinc-50">
                    {display(p.market_value_usd, masked)}
                  </span>
                  <span
                    className={`text-right text-sm tabular-nums ${
                      pl >= 0
                        ? "text-[#006300] dark:text-[#0ca30c]"
                        : "text-[#d03b3b]"
                    }`}
                  >
                    {masked ? "•••" : `${pl >= 0 ? "+" : ""}${usd(p.unrealized_pl_usd)}`}
                  </span>
                </summary>
                <div className="pb-2 pl-4">
                  {p.brokers.map((b) => (
                    <p
                      key={b.broker}
                      className="text-xs text-zinc-500 dark:text-zinc-400"
                    >
                      {b.broker}: {b.qty}
                    </p>
                  ))}
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
