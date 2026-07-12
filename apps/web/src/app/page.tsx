import { signOut } from "../auth";
import { getViewerContext } from "@/lib/viewerContext";
import { workerFetchRaw } from "@/lib/worker";
import {
  positionLabel,
  type Basket,
  type Portfolio,
  type PortfolioAccount,
  type SnapshotPoint,
} from "@/lib/portfolio";
import BasketCards from "@/components/BasketCards";
import NavTabs from "@/components/NavTabs";
import PortfolioHeader from "@/components/PortfolioHeader";
import AllocationBar from "@/components/AllocationBar";
import PositionTable from "@/components/PositionTable";
import ValueChart from "@/components/ValueChart";
import type { FlowPoint } from "@/lib/valueHistory";
import AsOfStamp from "@/components/AsOfStamp";
import RhRefreshButton from "@/components/RhRefreshButton";

/** Freshest sync across accounts; null = never synced (or no data). */
function lastSyncedAt(accounts: PortfolioAccount[] | undefined): string | null {
  const times = (accounts ?? [])
    .map((a) => a.last_synced_at)
    .filter((t): t is string => t !== null);
  if (times.length === 0) return null;
  return times.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b));
}

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
      ? "border-amber-400/40 text-amber-400"
      : "border-loss/40 text-loss";
  return (
    <div
      role="status"
      className={`rounded-lg border bg-card px-4 py-2.5 text-sm ${cls}`}
    >
      {children}
    </div>
  );
}

export default async function Home() {
  // Effective view: an owner in viewer-preview renders as a masked viewer
  // (owner tools hidden) — server actions still enforce the real role.
  const { email, role, masked } = await getViewerContext();

  const [{ status, body }, snapshotsRes, basketsRes, flowsRes] = await Promise.all([
    workerFetchRaw("/internal/portfolio"),
    workerFetchRaw("/internal/snapshots?days=90"),
    workerFetchRaw("/internal/baskets"),
    workerFetchRaw("/internal/cashflows"),
  ]);
  const snapshots =
    snapshotsRes.status === 200 ? (snapshotsRes.body as SnapshotPoint[]) : null;
  const flows =
    flowsRes.status === 200 && Array.isArray(flowsRes.body)
      ? (flowsRes.body as FlowPoint[])
      : [];
  // Non-200 (e.g. a worker that predates baskets) silently hides the section.
  const baskets =
    basketsRes.status === 200 && Array.isArray(basketsRes.body)
      ? (basketsRes.body as Basket[])
      : [];
  const rhAuthExpired =
    status === 502 && (body as { error?: string } | null)?.error === "rh_auth";
  const portfolio = status === 200 ? (body as Portfolio) : null;

  async function handleSignOut() {
    "use server";
    await signOut({ redirectTo: "/login" });
  }

  return (
    <>
      {/* Slim sticky nav on a blurred surface. */}
      <header className="sticky top-0 z-10 border-b border-hairline bg-surface/80 backdrop-blur">
        <div className="mx-auto flex h-12 w-full max-w-5xl items-center gap-4 px-4 sm:px-6">
          <span className="text-sm font-semibold text-ink">broker-cockpit</span>
          {/* NavTabs renders the desktop bar AND (below sm) the hamburger
              dropdown; the sign-out action rides into the mobile drawer. */}
          <NavTabs active="/" signOut={handleSignOut} />
          <div className="ml-auto hidden items-center gap-4 text-[13px] text-ink-2 sm:flex">
            <span>
              {email} ({role ?? "no role"})
            </span>
            <form action={handleSignOut}>
              {/* Quiet text-link sign-out. */}
              <button
                type="submit"
                className="text-[13px] text-ink-2 transition-colors hover:text-ink"
              >
                Sign out
              </button>
            </form>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-4 py-10 font-sans sm:px-6">
        {rhAuthExpired && (
          <Banner tone="red">
            Robinhood session expired — use the &ldquo;Refresh Robinhood
            session&rdquo; button below (owner only).
          </Banner>
        )}

        {!portfolio && !rhAuthExpired && (
          <Banner tone="red">Portfolio data unavailable (worker returned {status}).</Banner>
        )}

        {portfolio && staleMessage(portfolio.accounts) && (
          <Banner tone="amber">{staleMessage(portfolio.accounts)}</Banner>
        )}

        {portfolio && (
          <PortfolioHeader
            totalValueUsd={portfolio.total_value_usd}
            dayChangeUsd={portfolio.day_change_usd}
            dayChangePct={portfolio.day_change_pct}
            cashUsd={portfolio.cash_usd}
            masked={masked}
          />
        )}

        {/* Muted utility row: as-of stamp + RH session refresh. The button
            renders for every role — the server action re-verifies the REAL
            owner role and returns the clean permission error for viewers.
            The prefilled RH username stays owner-only (it must not leak to
            viewers or to owner-in-preview). Rendered even when the portfolio
            fetch failed: that's exactly when the owner needs the refresh. */}
        <div className="-mt-6 flex flex-wrap items-start justify-between gap-3">
          <AsOfStamp lastSyncedAt={lastSyncedAt(portfolio?.accounts)} />
          <RhRefreshButton
            defaultUsername={role === "owner" ? process.env.RH_USERNAME ?? "" : ""}
          />
        </div>

        {portfolio && (
          <>
            {snapshots !== null && (
              <ValueChart snapshots={snapshots} masked={masked} flows={flows} />
            )}

            <AllocationBar
              items={[
                ...portfolio.positions.map((p) => ({
                  label: positionLabel(p),
                  weightPct: Number(p.weight_pct),
                  tags: p.tags,
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

            <BasketCards baskets={baskets} masked={masked} />

            <PositionTable positions={portfolio.positions} masked={masked} />
          </>
        )}
      </main>
    </>
  );
}
