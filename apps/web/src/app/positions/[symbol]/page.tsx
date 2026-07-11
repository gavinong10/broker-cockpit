import Link from "next/link";
import { notFound } from "next/navigation";
import { auth } from "@/auth";
import { display, displayQty, usd } from "@/lib/format";
import { positionLabel, type PositionDetail } from "@/lib/portfolio";
import { isMasked } from "@/lib/roles";
import { workerFetchRaw } from "@/lib/worker";

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  const color =
    tone === "up" ? "text-gain" : tone === "down" ? "text-loss" : "text-ink";
  return (
    <div>
      <p className="micro-label">{label}</p>
      <p className={`mt-1 text-lg font-medium tabular-nums ${color}`}>{value}</p>
    </div>
  );
}

export default async function PositionPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;

  const session = await auth();
  const u = session?.user as
    | { role?: "owner" | "viewer" | null; mask_amounts?: boolean }
    | undefined;
  const masked = isMasked(u?.role ?? null, u?.mask_amounts);

  const { status, body } = await workerFetchRaw(
    `/internal/positions/${encodeURIComponent(symbol)}`,
  );
  if (status === 404) notFound();
  if (status !== 200) {
    return (
      <main className="mx-auto w-full max-w-5xl px-6 py-10 font-sans">
        <p className="rounded-lg border border-loss/40 bg-card px-4 py-2.5 text-sm text-loss">
          Position data unavailable (worker returned {status}).
        </p>
      </main>
    );
  }

  const detail = body as PositionDetail;
  const pl = Number(detail.unrealized_pl_usd);
  const day = Number(detail.day_change_usd);

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-6 py-10 font-sans">
      <div>
        <Link
          href="/"
          className="text-[13px] text-ink-2 underline-offset-2 transition-colors hover:text-ink hover:underline"
        >
          &larr; Portfolio
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">
          {positionLabel(detail)}
        </h1>
        {detail.sec_type === "OPT" && (
          <p className="mt-0.5 font-mono text-xs text-ink-3">{detail.symbol}</p>
        )}
      </div>

      <section
        aria-label="Aggregate position stats"
        className="grid grid-cols-2 gap-4 sm:grid-cols-4"
      >
        <Stat label="Quantity" value={displayQty(detail.qty, masked)} />
        <Stat
          label="Avg cost"
          value={detail.avg_cost_usd === null ? "—" : display(detail.avg_cost_usd, masked)}
        />
        <Stat label="Market value" value={display(detail.market_value_usd, masked)} />
        <Stat
          label="Unrealized P/L"
          value={masked ? "•••" : `${pl >= 0 ? "+" : ""}${usd(detail.unrealized_pl_usd)}`}
          tone={pl >= 0 ? "up" : "down"}
        />
      </section>

      <p className="text-sm text-ink-2">
        Last price{" "}
        {detail.last_price_usd === null ? "—" : display(detail.last_price_usd, masked)}
        {" · "}day change{" "}
        <span
          className={day >= 0 ? "text-gain" : "text-loss"}
        >
          {masked ? "•••" : `${day >= 0 ? "+" : ""}${usd(detail.day_change_usd)}`}
        </span>
      </p>

      <section aria-label="Per-account breakdown">
        <h2 className="micro-label mb-3">Accounts</h2>
        <div className="grid grid-cols-[repeat(6,minmax(5rem,1fr))] gap-2 border-b border-hairline pb-2">
          {["Broker", "Account", "Qty", "Avg cost", "Market value", "Unrealized P/L"].map(
            (h, i) => (
              <span key={h} className={`micro-label ${i < 2 ? "" : "text-right"}`}>
                {h}
              </span>
            ),
          )}
        </div>
        <ul>
          {detail.accounts.map((a) => {
            const rowPl = Number(a.unrealized_pl_usd);
            return (
              <li
                key={`${a.broker}-${a.external_id}`}
                className="grid h-12 grid-cols-[repeat(6,minmax(5rem,1fr))] items-center gap-2 border-b border-hairline transition-colors last:border-b-0 hover:bg-hover"
              >
                <span className="text-sm text-ink">{a.broker}</span>
                <span className="text-sm text-ink-2">{a.external_id}</span>
                <span className="text-right text-sm tabular-nums text-ink">
                  {displayQty(a.qty, masked)}
                </span>
                <span className="text-right text-sm tabular-nums text-ink">
                  {a.avg_cost_usd === null ? "—" : display(a.avg_cost_usd, masked)}
                </span>
                <span className="text-right text-sm tabular-nums text-ink">
                  {display(a.market_value_usd, masked)}
                </span>
                <span
                  className={`text-right text-sm tabular-nums ${
                    rowPl >= 0 ? "text-gain" : "text-loss"
                  }`}
                >
                  {masked ? "•••" : `${rowPl >= 0 ? "+" : ""}${usd(a.unrealized_pl_usd)}`}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      <section
        aria-label="Journal"
        className="rounded-xl border border-dashed border-hairline px-4 py-6 text-center"
      >
        <h2 className="micro-label">Journal — coming in Phase 2</h2>
        <p className="mt-2 text-sm text-ink-3">
          Trade notes and thesis tracking will live here.
        </p>
      </section>
    </main>
  );
}
