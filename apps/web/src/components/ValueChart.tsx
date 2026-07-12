// Server component: portfolio value over time as an inline SVG line chart
// (no chart library). dataviz: single series -> one hue, chosen by window
// polarity (gain green when last >= first, loss orange-red otherwise —
// status color paired with real signed values, never color-alone). 2px line
// with round join/cap, soft vertical gradient wash under the line (8% ->
// transparent), >=8px end marker with a 2px surface ring, no chart borders
// or gridlines beyond a single recessive baseline, selective direct labels
// (min / max / latest only) in muted ink with tabular figures. No legend:
// one series, the heading names it. Dollar labels respect masking; the line
// shape (relative change) stays visible.

import { display } from "@/lib/format";
import type { SnapshotPoint } from "@/lib/portfolio";
import {
  depositsBaseline,
  hasBackfill,
  performanceExDeposits,
  type FlowPoint,
} from "@/lib/valueHistory";

const W = 640;
const H = 200;
const PAD = { top: 16, right: 12, bottom: 24, left: 12 };

const GAIN = "#00c805";
const LOSS = "#ff5000";

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

export default function ValueChart({
  snapshots,
  masked,
  title = "Portfolio value over time",
  flows = [],
}: {
  snapshots: SnapshotPoint[];
  masked: boolean;
  title?: string;
  flows?: FlowPoint[];
}) {
  const note = (text: string) => <p className="text-sm text-ink-2">{text}</p>;

  const heading = <h2 className="micro-label mb-3">{title}</h2>;

  if (snapshots.length === 0) {
    return (
      <section aria-label={title}>
        {heading}
        {note("No snapshots yet — history accumulates from go-live, one point per day.")}
      </section>
    );
  }

  const values = snapshots.map((s) => Number(s.total_value_usd));
  const depBaseline = depositsBaseline(snapshots, flows);
  const perf = performanceExDeposits(snapshots, flows);
  const scaleValues = depBaseline === null ? values : [...values, ...depBaseline];
  const lo = Math.min(...scaleValues);
  const hi = Math.max(...scaleValues);
  const span = hi - lo || Math.abs(hi) * 0.02 || 1; // flat series: give it air
  const yMin = lo - span * 0.15;
  const yMax = hi + span * 0.15;

  // Window polarity picks the single series hue (RH convention).
  const up = values[values.length - 1] >= values[0];
  const hue = up ? GAIN : LOSS;
  const gradId = up ? "vc-wash-gain" : "vc-wash-loss";

  const x = (i: number) =>
    snapshots.length === 1
      ? W / 2
      : PAD.left + (i / (snapshots.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) =>
    PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);

  const pts = values.map((v, i) => [x(i), y(v)] as const);
  const linePath = pts.map(([px, py], i) => `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const baseline = H - PAD.bottom;
  const areaPath = `${linePath} L${pts[pts.length - 1][0].toFixed(1)},${baseline} L${pts[0][0].toFixed(1)},${baseline} Z`;
  // Deposits baseline as a stepped dashed line: flows land between snapshot
  // days, so hold each level then step at the next point.
  const depPath = depBaseline === null ? null : depBaseline
    .map((v, i) => {
      const px = x(i).toFixed(1);
      const py = y(v).toFixed(1);
      if (i === 0) return `M${px},${py}`;
      return `L${px},${y(depBaseline[i - 1]).toFixed(1)} L${px},${py}`;
    })
    .join(" ");

  const last = snapshots.length - 1;
  const minIdx = values.indexOf(lo);
  const maxIdx = values.indexOf(hi);
  // Selective labels: latest always; min/max only when they aren't the latest
  // point (and aren't each other) so labels never pile up.
  const labelled = new Set([last, minIdx, maxIdx]);

  return (
    <section aria-label={title}>
      {heading}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Daily portfolio value, ${snapshots[0].taken_on} to ${snapshots[last].taken_on}`}
      >
        <defs>
          {/* soft vertical wash under the line: 8% -> transparent */}
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={hue} stopOpacity={0.08} />
            <stop offset="100%" stopColor={hue} stopOpacity={0} />
          </linearGradient>
        </defs>

        {/* single recessive baseline — no other gridlines, no border */}
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={baseline}
          y2={baseline}
          stroke="#23262e"
          strokeWidth={1}
        />

        {snapshots.length > 1 && (
          <>
            <path d={areaPath} fill={`url(#${gradId})`} />
            <path
              d={linePath}
              fill="none"
              stroke={hue}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </>
        )}

        {/* end marker: >=8px dot with a 2px surface ring */}
        <circle
          cx={pts[last][0]}
          cy={pts[last][1]}
          r={4}
          fill={hue}
          stroke="#0e0f13"
          strokeWidth={2}
        />

        {/* min / max / latest direct labels in text tokens, never series color */}
        {[...labelled].map((i) => {
          const anchor = i === 0 ? "start" : i === last ? "end" : "middle";
          const above = i === minIdx && i !== maxIdx ? false : true;
          return (
            <text
              key={i}
              x={pts[i][0]}
              y={above ? pts[i][1] - 8 : pts[i][1] + 14}
              textAnchor={anchor}
              className="fill-ink-2 text-[11px] tabular-nums"
            >
              {display(values[i], masked)}
            </text>
          );
        })}

        {/* x-axis: first and last dates */}
        <text
          x={PAD.left}
          y={H - 6}
          textAnchor="start"
          className="fill-ink-3 text-[11px] tabular-nums"
        >
          {shortDate(snapshots[0].taken_on)}
        </text>
        {snapshots.length > 1 && (
          <text
            x={W - PAD.right}
            y={H - 6}
            textAnchor="end"
            className="fill-ink-3 text-[11px] tabular-nums"
          >
            {shortDate(snapshots[last].taken_on)}
          </text>
        )}

        {/* native hover tooltips: generous invisible hit targets per point */}
        {pts.map(([px, py], i) => (
          <circle key={`hit-${i}`} cx={px} cy={py} r={12} fill="transparent">
            <title>
              {`${snapshots[i].taken_on}: ${display(values[i], masked)}`}
            </title>
          </circle>
        ))}
        {depPath !== null && (
          <path
            d={depPath}
            fill="none"
            stroke="var(--color-ink-3)"
            strokeWidth="1.5"
            strokeDasharray="4 4"
          >
            <title>Net deposits baseline</title>
          </path>
        )}
      </svg>
      {snapshots.length === 1 &&
        note("One snapshot so far — history accumulates from go-live, one point per day.")}
      {perf !== null && (
        <p className="mt-2 text-[13px] tabular-nums text-ink-2">
          Performance excl. deposits:{" "}
          <span className={perf.usd >= 0 ? "text-gain" : "text-loss"}>
            {masked
              ? "•••"
              : `${perf.usd >= 0 ? "+" : "-"}$${Math.abs(perf.usd).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}{" "}
            ({perf.pct >= 0 ? "+" : ""}
            {perf.pct.toFixed(2)}%)
          </span>{" "}
          <span className="text-ink-3">— dashed line = deposits baseline</span>
        </p>
      )}
      {hasBackfill(snapshots) && (
        <p className="mt-1 text-xs text-ink-3">
          Includes backfilled history (Robinhood equity records, pre go-live).
        </p>
      )}
    </section>
  );
}
