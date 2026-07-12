import Link from "next/link";
import { display, displayQty, pct } from "@/lib/format";
import type { ExposureConstituent, ExposureRow } from "@/lib/exposure";
import { positionLabel } from "@/lib/portfolio";
import TagChips from "@/components/TagChips";

// Validated categorical pair for the dark surface (dataviz palette slots 1-2,
// dark steps; CVD ΔE 69.8, contrast ≥3:1): stock=blue, options=aqua.
const STOCK_HUE = "#3987e5";
const OPTION_HUE = "#199e70";

function Bar({ stockPct, optPct }: { stockPct: number; optPct: number }) {
  return (
    <div className="flex h-2 items-stretch gap-[2px]" aria-hidden>
      {stockPct > 0 && (
        <span className="rounded-full" style={{ width: `${stockPct}%`, background: STOCK_HUE }} />
      )}
      {optPct > 0 && (
        <span className="rounded-full" style={{ width: `${optPct}%`, background: OPTION_HUE }} />
      )}
    </div>
  );
}

function RowSummary({ r, masked, expandable, activeTag }: { r: ExposureRow; masked: boolean; expandable?: boolean; activeTag?: string | null }) {
  return (
    <div className="flex flex-col gap-0.5 text-[13px] sm:flex-row sm:items-baseline sm:justify-between sm:gap-2">
      <span className="flex min-w-0 items-baseline gap-2 text-ink tabular-nums">
        {r.underlying}
        <TagChips tags={r.tags} activeTag={activeTag} />
        {expandable && (
          <>
            <span className="text-[11px] text-ink-3 group-open:hidden">▸</span>
            <span className="hidden text-[11px] text-ink-3 group-open:inline">▾</span>
          </>
        )}
      </span>
      <span className="text-ink-2 tabular-nums">
        {display(r.total_usd, masked)}
        <span className="ml-2 text-ink-3">
          {pct(r.weight_pct)} · stock {display(r.stock_value_usd, masked)} · options{" "}
          {display(r.option_value_usd, masked)}
        </span>
      </span>
    </div>
  );
}

function Constituent({ c, masked }: { c: ExposureConstituent; masked: boolean }) {
  return (
    <li className="flex items-baseline justify-between gap-3 py-1 text-[13px]">
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-ink-2">{positionLabel(c)}</span>
        {c.sec_type === "OPT" && (
          <span className="hidden text-[11px] text-ink-3 sm:inline">{c.symbol}</span>
        )}
        {c.baskets.map((b) => (
          <Link
            key={b.slug}
            href={`/baskets/${b.slug}`}
            className="rounded-full border border-accent/40 px-1.5 py-px text-[10px] leading-4 text-accent hover:border-accent"
          >
            {b.slug}
          </Link>
        ))}
      </span>
      <span className="shrink-0 tabular-nums text-ink-2">
        {displayQty(c.qty, masked)} <span className="text-ink-3">·</span>{" "}
        {display(c.market_value_usd, masked)}
      </span>
    </li>
  );
}

/** Stacked horizontal bars: dollar exposure per underlying, stock + options.
 *
 * Bar geometry uses the positive components only (a net-short options line
 * subtracts in the printed numbers but is not drawn as negative width); exact
 * signed values always appear in the row labels, which is the table view.
 * Dollar labels respect masking; bar lengths are relative (like allocation
 * weights, which viewers already see). Rows expand (server-side <details>,
 * matching the home page) to the per-position breakdown with basket chips;
 * the synthetic "Other" row expands to its folded underlyings on the same
 * bar scale.
 */
export default function ExposureChart({
  rows,
  masked,
  activeTag,
}: {
  rows: ExposureRow[];
  masked: boolean;
  activeTag?: string | null;
}) {
  const maxTotal = Math.max(
    ...rows.map((r) => Math.max(0, Number(r.stock_value_usd)) + Math.max(0, Number(r.option_value_usd))),
    1,
  );
  const geometry = (r: ExposureRow) => ({
    stockPct: (Math.max(0, Number(r.stock_value_usd)) / maxTotal) * 100,
    optPct: (Math.max(0, Number(r.option_value_usd)) / maxTotal) * 100,
  });
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
          const expandable =
            (r.positions?.length ?? 0) > 0 || (r.others?.length ?? 0) > 0;
          if (!expandable) {
            return (
              <li key={r.underlying} className="flex flex-col gap-1">
                <RowSummary r={r} masked={masked} activeTag={activeTag} />
                <Bar {...geometry(r)} />
              </li>
            );
          }
          return (
            <li key={r.underlying}>
              <details className="group">
                <summary className="-mx-1 flex cursor-pointer list-none flex-col gap-1 rounded-md px-1 py-0.5 hover:bg-hover [&::-webkit-details-marker]:hidden">
                  <RowSummary r={r} masked={masked} expandable activeTag={activeTag} />
                  <Bar {...geometry(r)} />
                </summary>
                {r.positions && r.positions.length > 0 && (
                  <ul className="ml-1 mt-1 border-l border-hairline pl-4">
                    {r.positions.map((c) => (
                      <Constituent key={`${c.symbol}-${c.sec_type}`} c={c} masked={masked} />
                    ))}
                  </ul>
                )}
                {r.others && r.others.length > 0 && (
                  <ul className="ml-1 mt-1 flex flex-col gap-2 border-l border-hairline pl-4">
                    {r.others.map((o) => (
                      <li key={o.underlying} className="flex flex-col gap-1">
                        <RowSummary r={o} masked={masked} activeTag={activeTag} />
                        <Bar {...geometry(o)} />
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
