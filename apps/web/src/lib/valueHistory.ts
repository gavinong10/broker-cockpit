// Pure helpers for the deposits-vs-performance chart treatment.
// Kept out of components so they get vitest coverage.

import type { SnapshotPoint } from "./portfolio";

export type FlowPoint = { occurred_on: string; net_usd: string };

/** Deposits baseline: what the portfolio would be worth if every external
 * flow since the first snapshot had just sat as cash — i.e. first value +
 * cumulative net flows dated after the first snapshot, stepped per snapshot
 * date. Returns null when there are no in-window flows (overlay is noise). */
export function depositsBaseline(
  snapshots: SnapshotPoint[],
  flows: FlowPoint[],
): number[] | null {
  if (snapshots.length < 2 || flows.length === 0) return null;
  const start = snapshots[0].taken_on;
  const inWindow = flows.filter((f) => f.occurred_on > start);
  if (inWindow.length === 0) return null;
  const base = Number(snapshots[0].total_value_usd);
  return snapshots.map((s) => {
    let cum = 0;
    for (const f of inWindow) {
      if (f.occurred_on <= s.taken_on) cum += Number(f.net_usd);
    }
    return base + cum;
  });
}

/** Performance excluding deposits: latest value minus the deposits baseline.
 * pct is relative to the baseline (capital actually put in). */
export function performanceExDeposits(
  snapshots: SnapshotPoint[],
  flows: FlowPoint[],
): { usd: number; pct: number } | null {
  const baseline = depositsBaseline(snapshots, flows);
  if (baseline === null) return null;
  const last = Number(snapshots[snapshots.length - 1].total_value_usd);
  const ref = baseline[baseline.length - 1];
  if (!ref) return null;
  return { usd: last - ref, pct: ((last - ref) / Math.abs(ref)) * 100 };
}

/** True when any point is backfilled (disclosed under the chart). */
export function hasBackfill(snapshots: SnapshotPoint[]): boolean {
  return snapshots.some((s) => s.source === "backfill_rh");
}
