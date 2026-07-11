import { display, pct } from "@/lib/format";
import type { ExposureRow } from "@/lib/exposure";

// Validated categorical pair for the dark surface (dataviz palette slots 1-2,
// dark steps; CVD ΔE 69.8, contrast ≥3:1): stock=blue, options=aqua.
const STOCK_HUE = "#3987e5";
const OPTION_HUE = "#199e70";

/** Stacked horizontal bars: dollar exposure per underlying, stock + options.
 *
 * Bar geometry uses the positive components only (a net-short options line
 * subtracts in the printed numbers but is not drawn as negative width); exact
 * signed values always appear in the row labels, which is the table view.
 * Dollar labels respect masking; bar lengths are relative (like allocation
 * weights, which viewers already see).
 */
export default function ExposureChart({
  rows,
  masked,
}: {
  rows: ExposureRow[];
  masked: boolean;
}) {
  const maxTotal = Math.max(
    ...rows.map((r) => Math.max(0, Number(r.stock_value_usd)) + Math.max(0, Number(r.option_value_usd))),
    1,
  );
  return (
    <section aria-label="Dollar exposure by underlying" className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-2">
          Exposure by underlying
        </h2>
        <div className="flex items-center gap-4 text-[12px] text-ink-2">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: STOCK_HUE }} />
            Stock
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: OPTION_HUE }} />
            Options
          </span>
        </div>
      </div>
      <ul className="flex flex-col gap-3">
        {rows.map((r) => {
          const stock = Math.max(0, Number(r.stock_value_usd));
          const opt = Math.max(0, Number(r.option_value_usd));
          const stockPct = (stock / maxTotal) * 100;
          const optPct = (opt / maxTotal) * 100;
          return (
            <li key={r.underlying} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between text-[13px]">
                <span className="text-ink tabular-nums">{r.underlying}</span>
                <span className="text-ink-2 tabular-nums">
                  {display(r.total_usd, masked)}
                  <span className="ml-2 text-ink-3">
                    {pct(r.weight_pct)} · stock {display(r.stock_value_usd, masked)} · options{" "}
                    {display(r.option_value_usd, masked)}
                  </span>
                </span>
              </div>
              <div className="flex h-2 items-stretch gap-[2px]" aria-hidden>
                {stockPct > 0 && (
                  <span
                    className="rounded-full"
                    style={{ width: `${stockPct}%`, background: STOCK_HUE }}
                  />
                )}
                {optPct > 0 && (
                  <span
                    className="rounded-full"
                    style={{ width: `${optPct}%`, background: OPTION_HUE }}
                  />
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
