// Server component: portfolio value over time as an inline SVG line chart
// (no chart library). dataviz: single series -> slot-1 blue (light #2a78d6 /
// dark #3987e5), 2px line with round join/cap, ~10%-opacity area wash, 8px
// end marker with a 2px surface ring, solid hairline gridlines, selective
// direct labels (min / max / latest only), axis text in muted ink with
// tabular figures. No legend: one series, the heading names it. Dollar
// labels respect masking; the line shape (relative change) stays visible.

import { display } from "@/lib/format";
import type { SnapshotPoint } from "@/lib/portfolio";

const W = 640;
const H = 200;
const PAD = { top: 16, right: 12, bottom: 24, left: 12 };

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

export default function ValueChart({
  snapshots,
  masked,
  title = "Portfolio value over time",
}: {
  snapshots: SnapshotPoint[];
  masked: boolean;
  title?: string;
}) {
  const note = (text: string) => (
    <p className="text-sm text-zinc-500 dark:text-zinc-400">{text}</p>
  );

  const heading = (
    <h2 className="mb-2 text-sm font-medium text-zinc-500 dark:text-zinc-400">
      {title}
    </h2>
  );

  if (snapshots.length === 0) {
    return (
      <section aria-label={title}>
        {heading}
        {note("No snapshots yet — history accumulates from go-live, one point per day.")}
      </section>
    );
  }

  const values = snapshots.map((s) => Number(s.total_value_usd));
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || Math.abs(hi) * 0.02 || 1; // flat series: give it air
  const yMin = lo - span * 0.15;
  const yMax = hi + span * 0.15;

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
        {/* hairline gridlines, solid, recessive */}
        {[0.25, 0.5, 0.75].map((f) => {
          const gy = PAD.top + f * (H - PAD.top - PAD.bottom);
          return (
            <line
              key={f}
              x1={PAD.left}
              x2={W - PAD.right}
              y1={gy}
              y2={gy}
              className="stroke-zinc-200 dark:stroke-zinc-800"
              strokeWidth={1}
            />
          );
        })}
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={baseline}
          y2={baseline}
          className="stroke-zinc-300 dark:stroke-zinc-700"
          strokeWidth={1}
        />

        {snapshots.length > 1 && (
          <>
            <path d={areaPath} className="fill-[#2a78d6]/10 dark:fill-[#3987e5]/10" />
            <path
              d={linePath}
              fill="none"
              className="stroke-[#2a78d6] dark:stroke-[#3987e5]"
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
          className="fill-[#2a78d6] stroke-white dark:fill-[#3987e5] dark:stroke-[#0a0a0a]"
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
              className="fill-zinc-500 text-[11px] tabular-nums dark:fill-zinc-400"
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
          className="fill-zinc-500 text-[11px] tabular-nums dark:fill-zinc-400"
        >
          {shortDate(snapshots[0].taken_on)}
        </text>
        {snapshots.length > 1 && (
          <text
            x={W - PAD.right}
            y={H - 6}
            textAnchor="end"
            className="fill-zinc-500 text-[11px] tabular-nums dark:fill-zinc-400"
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
      </svg>
      {snapshots.length === 1 &&
        note("One snapshot so far — history accumulates from go-live, one point per day.")}
    </section>
  );
}
