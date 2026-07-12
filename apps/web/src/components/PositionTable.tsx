// Server component: positions table in API order (market value desc).
// Each row is a <details> element (stays server-side) whose summary is the
// data row; expanding reveals the per-broker quantity breakdown. The
// symbol/label cell links to the position detail page. Dollar cells AND qty
// cells respect masking (qty x public per-share price reconstructs dollars).
// Option rows show the human label with the raw OCC symbol as a faint
// sub-line. Gain/loss color always rides with an explicit +/- sign.

import Link from "next/link";
import { display, displayQty, usd } from "@/lib/format";
import { positionLabel, type PortfolioPosition } from "@/lib/portfolio";

const GRID =
  "grid grid-cols-[minmax(9rem,2fr)_repeat(5,minmax(5.5rem,1fr))] items-center gap-2";

function DayChange({ value, masked }: { value: string; masked: boolean }) {
  const n = Number(value);
  const color = n >= 0 ? "text-gain" : "text-loss";
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
      <p className="text-sm text-ink-2">
        No positions yet — they appear after the first broker sync.
      </p>
    );
  }

  return (
    <section aria-label="Positions">
      <h2 className="micro-label mb-3">Positions</h2>
      {/* Many numeric columns overflow a phone; scroll the TABLE, not the page. */}
      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <div className="min-w-[34rem]">
      <div className={`${GRID} border-b border-hairline pb-2`}>
        {["Symbol", "Qty", "Last price", "Day change", "Market value", "Unrealized P/L"].map(
          (h, i) => (
            <span
              key={h}
              className={`micro-label ${i === 0 ? "" : "text-right"}`}
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
              <details className="group border-b border-hairline last:border-b-0">
                <summary
                  className={`${GRID} h-12 cursor-pointer list-none transition-colors hover:bg-hover`}
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <Link
                        href={`/positions/${encodeURIComponent(p.symbol)}`}
                        className="truncate text-sm font-medium text-ink underline-offset-2 hover:underline"
                      >
                        {label}
                      </Link>
                      {/* Basket chips: absent field = worker predates baskets. */}
                      {(p.baskets ?? []).map((b) => (
                        <Link
                          key={b.slug}
                          href={`/baskets/${encodeURIComponent(b.slug)}`}
                          className="rounded-full border border-accent/40 px-2 py-px text-[10px] text-accent underline-offset-2 hover:underline"
                        >
                          {b.slug}
                        </Link>
                      ))}
                    </span>
                    {/* OCC symbol as a faint sub-line under option labels. */}
                    {p.sec_type === "OPT" && (
                      <span className="block truncate text-[11px] leading-4 text-ink-3">
                        {p.symbol}
                      </span>
                    )}
                  </span>
                  <span className="text-right text-sm tabular-nums text-ink">
                    {displayQty(p.qty, masked)}
                  </span>
                  <span className="text-right text-sm tabular-nums text-ink">
                    {p.last_price_usd === null ? "—" : display(p.last_price_usd, masked)}
                  </span>
                  <DayChange value={p.day_change_usd} masked={masked} />
                  <span className="text-right text-sm tabular-nums text-ink">
                    {display(p.market_value_usd, masked)}
                  </span>
                  <span
                    className={`text-right text-sm tabular-nums ${
                      pl >= 0 ? "text-gain" : "text-loss"
                    }`}
                  >
                    {masked ? "•••" : `${pl >= 0 ? "+" : ""}${usd(p.unrealized_pl_usd)}`}
                  </span>
                </summary>
                {/* Quiet indented sub-row: per-broker breakdown. */}
                <div className="border-l border-hairline pb-3 pl-4 ml-1">
                  {p.brokers.map((b) => (
                    <p key={b.broker} className="text-xs leading-5 text-ink-2">
                      {b.broker}: {displayQty(b.qty, masked)}
                    </p>
                  ))}
                </div>
              </details>
            </li>
          );
        })}
      </ul>
      </div>
      </div>
    </section>
  );
}
