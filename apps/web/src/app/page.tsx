import { auth, signOut } from "../auth";
import { workerFetchRaw } from "@/lib/worker";
import {
  positionLabel,
  type Portfolio,
  type PortfolioAccount,
  type SnapshotPoint,
} from "@/lib/portfolio";
import PortfolioHeader from "@/components/PortfolioHeader";
import AllocationBar from "@/components/AllocationBar";
import PositionTable from "@/components/PositionTable";
import ValueChart from "@/components/ValueChart";

function staleMessage(accounts: PortfolioAccount[]): string | null {
  const stale = accounts.filter((a) => a.stale);
  if (stale.length === 0) return null;
  const parts = stale.map((a) => {
    if (!a.last_synced_at) return `${a.broker} never synced`;
    const mins = Math.round((Date.now() - Date.parse(a.last_synced_at)) / 60000);
    return `${a.broker} last synced ${mins} min ago`;
  });
  return `Data stale — ${parts.join("; ")}`;
}

function Banner({ tone, children }: { tone: "amber" | "red"; children: React.ReactNode }) {
  const cls =
    tone === "amber"
      ? "border-amber-400/60 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-200"
      : "border-red-400/60 bg-red-50 text-red-900 dark:border-red-500/40 dark:bg-red-950/40 dark:text-red-200";
  return (
    <div role="status" className={`rounded-md border px-4 py-2 text-sm ${cls}`}>
      {children}
    </div>
  );
}

export default async function Home() {
  const session = await auth();
  const email = session?.user?.email ?? "unknown";
  const user = session?.user as
    | { role?: "owner" | "viewer" | null; mask_amounts?: boolean }
    | undefined;
  const role = user?.role ?? null;
  const masked = user?.mask_amounts ?? false;

  const [{ status, body }, snapshotsRes] = await Promise.all([
    workerFetchRaw("/internal/portfolio"),
    workerFetchRaw("/internal/snapshots?days=90"),
  ]);
  const snapshots =
    snapshotsRes.status === 200 ? (snapshotsRes.body as SnapshotPoint[]) : null;
  const rhAuthExpired =
    status === 502 && (body as { error?: string } | null)?.error === "rh_auth";
  const portfolio = status === 200 ? (body as Portfolio) : null;

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-10 font-sans">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-zinc-950 dark:text-zinc-50">
          broker-cockpit
        </h1>
        <div className="flex items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
          <span>
            {email} ({role ?? "no role"})
          </span>
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/login" });
            }}
          >
            <button
              type="submit"
              className="rounded-md border border-zinc-300 px-3 py-1 text-sm text-zinc-950 dark:border-zinc-700 dark:text-zinc-50"
            >
              Sign out
            </button>
          </form>
        </div>
      </div>

      {rhAuthExpired && (
        <Banner tone="red">
          Robinhood session expired — re-run <code>rh_login.py</code> on the Mac
          and copy the new session file to the server.
        </Banner>
      )}

      {!portfolio && !rhAuthExpired && (
        <Banner tone="red">Portfolio data unavailable (worker returned {status}).</Banner>
      )}

      {portfolio && (
        <>
          {staleMessage(portfolio.accounts) && (
            <Banner tone="amber">{staleMessage(portfolio.accounts)}</Banner>
          )}

          <PortfolioHeader
            totalValueUsd={portfolio.total_value_usd}
            dayChangeUsd={portfolio.day_change_usd}
            dayChangePct={portfolio.day_change_pct}
            cashUsd={portfolio.cash_usd}
            masked={masked}
          />

          <AllocationBar
            items={[
              ...portfolio.positions.map((p) => ({
                label: positionLabel(p),
                weightPct: Number(p.weight_pct),
              })),
              {
                label: "Cash",
                weightPct:
                  Number(portfolio.total_value_usd) !== 0
                    ? (Number(portfolio.cash_usd) / Number(portfolio.total_value_usd)) * 100
                    : 0,
              },
            ]}
          />

          {snapshots !== null && <ValueChart snapshots={snapshots} masked={masked} />}

          <PositionTable positions={portfolio.positions} masked={masked} />
        </>
      )}
    </main>
  );
}
