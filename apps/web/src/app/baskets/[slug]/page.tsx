// Basket ("campaign") mini-portfolio page: thesis / horizon / invalidation
// header, then the scoped view reusing the dashboard components — value chart
// from basket_snapshots, allocation bars, position table. Dollars and qty
// respect masking; percents (allocation weights, P/L %) are always real.

import Link from "next/link";
import { notFound } from "next/navigation";
import { auth } from "@/auth";
import { display, pct, usd } from "@/lib/format";
import { positionLabel, type BasketDetail } from "@/lib/portfolio";
import { isMasked } from "@/lib/roles";
import { workerFetchRaw } from "@/lib/worker";
import AllocationBar from "@/components/AllocationBar";
import PositionTable from "@/components/PositionTable";
import ValueChart from "@/components/ValueChart";

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

function TextBlock({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </h2>
      <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-950 dark:text-zinc-50">
        {text}
      </p>
    </div>
  );
}

export default async function BasketPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;

  const session = await auth();
  const u = session?.user as
    | { role?: "owner" | "viewer" | null; mask_amounts?: boolean }
    | undefined;
  const masked = isMasked(u?.role ?? null, u?.mask_amounts);

  const { status, body } = await workerFetchRaw(
    `/internal/baskets/${encodeURIComponent(slug)}`,
  );
  if (status === 404) notFound();
  if (status !== 200) {
    return (
      <main className="mx-auto w-full max-w-4xl px-6 py-10">
        <p className="text-sm text-red-700 dark:text-red-300">
          Basket data unavailable (worker returned {status}).
        </p>
      </main>
    );
  }

  const basket = body as BasketDetail;
  const pl = Number(basket.pl_usd);
  const plPct = Number(basket.pl_pct);

  // Allocation weights computed here from scoped market values: real
  // percents for every role, even when dollar amounts are masked.
  const totalMv = basket.positions.reduce(
    (sum, p) => sum + Number(p.market_value_usd),
    0,
  );
  const allocation = basket.positions.map((p) => ({
    label: positionLabel(p),
    weightPct: totalMv !== 0 ? (Number(p.market_value_usd) / totalMv) * 100 : 0,
  }));

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-10 font-sans">
      <div>
        <Link
          href="/"
          className="text-sm text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          &larr; Portfolio
        </Link>
        <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold text-zinc-950 dark:text-zinc-50">
          {basket.name}
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs uppercase text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            {basket.status}
          </span>
        </h1>
        {basket.source_ref !== null && (
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Imported from conversation {basket.source_ref}
          </p>
        )}
      </div>

      <section aria-label="Basket thesis" className="flex flex-col gap-4">
        <TextBlock label="Thesis" text={basket.thesis} />
        {basket.horizon !== null && (
          <TextBlock label="Horizon" text={basket.horizon} />
        )}
        {basket.invalidation !== null && (
          <TextBlock label="Invalidation" text={basket.invalidation} />
        )}
      </section>

      <section
        aria-label="Basket stats"
        className="grid grid-cols-2 gap-4 sm:grid-cols-4"
      >
        <Stat label="Deployed" value={display(basket.deployed_usd, masked)} />
        <Stat
          label="Current value"
          value={display(basket.current_value_usd, masked)}
        />
        <Stat
          label="P/L"
          value={masked ? "•••" : `${pl >= 0 ? "+" : ""}${usd(basket.pl_usd)}`}
          tone={pl >= 0 ? "up" : "down"}
        />
        <Stat
          label="P/L %"
          value={`${plPct >= 0 ? "+" : ""}${pct(basket.pl_pct)}`}
          tone={plPct >= 0 ? "up" : "down"}
        />
      </section>

      <ValueChart
        title="Basket value over time"
        snapshots={basket.snapshots.map((s) => ({
          taken_on: s.taken_on,
          total_value_usd: s.value_usd,
        }))}
        masked={masked}
      />

      {allocation.length > 0 && <AllocationBar items={allocation} />}

      <PositionTable positions={basket.positions} masked={masked} />
    </main>
  );
}
