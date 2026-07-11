import Link from "next/link";
import { notFound } from "next/navigation";
import { auth } from "@/auth";
import { display, usd } from "@/lib/format";
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
    tone === "up"
      ? "text-[#006300] dark:text-[#0ca30c]"
      : tone === "down"
        ? "text-[#d03b3b]"
        : "text-zinc-950 dark:text-zinc-50";
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </p>
      <p className={`text-lg font-medium tabular-nums ${color}`}>{value}</p>
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
      <main className="mx-auto w-full max-w-4xl px-6 py-10">
        <p className="text-sm text-red-700 dark:text-red-300">
          Position data unavailable (worker returned {status}).
        </p>
      </main>
    );
  }

  const detail = body as PositionDetail;
  const pl = Number(detail.unrealized_pl_usd);
  const day = Number(detail.day_change_usd);

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-10 font-sans">
      <div>
        <Link
          href="/"
          className="text-sm text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          &larr; Portfolio
        </Link>
        <h1 className="mt-2 text-2xl font-semibold text-zinc-950 dark:text-zinc-50">
          {positionLabel(detail)}
        </h1>
        {detail.sec_type === "OPT" && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">{detail.symbol}</p>
        )}
      </div>

      <section
        aria-label="Aggregate position stats"
        className="grid grid-cols-2 gap-4 sm:grid-cols-4"
      >
        <Stat label="Quantity" value={detail.qty} />
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

      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        Last price{" "}
        {detail.last_price_usd === null ? "—" : display(detail.last_price_usd, masked)}
        {" · "}day change{" "}
        <span
          className={day >= 0 ? "text-[#006300] dark:text-[#0ca30c]" : "text-[#d03b3b]"}
        >
          {masked ? "•••" : `${day >= 0 ? "+" : ""}${usd(detail.day_change_usd)}`}
        </span>
      </p>

      <section aria-label="Per-account breakdown">
        <h2 className="mb-2 text-sm font-medium text-zinc-500 dark:text-zinc-400">
          Accounts
        </h2>
        <div className="grid grid-cols-[repeat(6,minmax(5rem,1fr))] gap-2 border-b border-zinc-200 pb-1 dark:border-zinc-800">
          {["Broker", "Account", "Qty", "Avg cost", "Market value", "Unrealized P/L"].map(
            (h, i) => (
              <span
                key={h}
                className={`text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400 ${
                  i < 2 ? "" : "text-right"
                }`}
              >
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
                className="grid grid-cols-[repeat(6,minmax(5rem,1fr))] gap-2 border-b border-zinc-100 py-2 dark:border-zinc-900"
              >
                <span className="text-sm text-zinc-950 dark:text-zinc-50">{a.broker}</span>
                <span className="text-sm text-zinc-500 dark:text-zinc-400">
                  {a.external_id}
                </span>
                <span className="text-right text-sm tabular-nums text-zinc-950 dark:text-zinc-50">
                  {a.qty}
                </span>
                <span className="text-right text-sm tabular-nums text-zinc-950 dark:text-zinc-50">
                  {a.avg_cost_usd === null ? "—" : display(a.avg_cost_usd, masked)}
                </span>
                <span className="text-right text-sm tabular-nums text-zinc-950 dark:text-zinc-50">
                  {display(a.market_value_usd, masked)}
                </span>
                <span
                  className={`text-right text-sm tabular-nums ${
                    rowPl >= 0 ? "text-[#006300] dark:text-[#0ca30c]" : "text-[#d03b3b]"
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
        className="rounded-md border border-dashed border-zinc-300 px-4 py-6 text-center dark:border-zinc-700"
      >
        <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
          Journal — coming in Phase 2
        </h2>
        <p className="mt-1 text-sm text-zinc-400 dark:text-zinc-500">
          Trade notes and thesis tracking will live here.
        </p>
      </section>
    </main>
  );
}
