"use client";

// Flow-adjusted performance: headline (dollar P&L / money-weighted return / net
// contributions) + a period toggle, over an overlay chart (portfolio value line
// with its reconstructed pre-go-live tail drawn distinct, plus a net-
// contributions step line so the $280k deposit reads as a CONTRIBUTION step,
// never as performance). dataviz: one value series -> one hue by window polarity
// (gain green / loss orange-red); the contributions baseline is a neutral
// reference line in muted ink (a text token, not a competing categorical hue),
// so identity is never color-alone. Time-based x-scale so both series align on
// real dates. Masking: dollar amounts (P&L, contributions, value labels) hide
// for masked viewers; RETURN PERCENTAGES always show (they reveal no absolute $).

import { useState } from "react";
import { display, usd } from "@/lib/format";
import {
  firstObservedIndex,
  PERIODS,
  type Performance,
  type Period,
} from "@/lib/performance";

const W = 640;
const H = 220;
const PAD = { top: 16, right: 14, bottom: 24, left: 14 };
const GAIN = "#00c805";
const LOSS = "#ff5000";

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

/** ISO YYYY-MM-DD -> epoch millis, TZ-agnostic (avoid Date parsing drift). */
function t(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

/** Signed percent string from a decimal-percent value ("42.35" -> "+42.35%"). */
function signedPct(v: string | null): string {
  if (v === null) return "—";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function Stat({
  label,
  children,
  tone,
  big = false,
  sub,
}: {
  label: string;
  children: React.ReactNode;
  tone?: "gain" | "loss";
  big?: boolean;
  sub?: React.ReactNode;
}) {
  const color =
    tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-ink";
  return (
    <div>
      <p className="micro-label">{label}</p>
      <p
        className={`mt-1 tabular-nums font-semibold ${color} ${
          big ? "text-[28px] leading-tight sm:text-[34px]" : "text-lg"
        }`}
      >
        {children}
      </p>
      {sub && <p className="mt-0.5 text-xs tabular-nums text-ink-3">{sub}</p>}
    </div>
  );
}

function Chart({ perf, masked }: { perf: Performance; masked: boolean }) {
  const vals = perf.value_series;
  if (vals.length < 2) {
    return (
      <p className="text-sm text-ink-2">
        Not enough history yet — the performance chart needs at least two daily
        points.
      </p>
    );
  }

  const contrib = perf.contributions_series;
  const numV = vals.map((p) => Number(p.value_usd));
  const numC = contrib.map((p) => Number(p.value_usd));

  // Shared time x-domain across both series; shared $ y-domain so the gap
  // between value and contributions reads directly as profit.
  const times = [...vals.map((p) => t(p.date)), ...contrib.map((p) => t(p.date))];
  const tMin = Math.min(...times);
  const tMax = Math.max(...times);
  const allY = [...numV, ...numC];
  const lo = Math.min(...allY);
  const hi = Math.max(...allY);
  const span = hi - lo || Math.abs(hi) * 0.02 || 1;
  const yMin = lo - span * 0.12;
  const yMax = hi + span * 0.12;

  const x = (ms: number) =>
    tMax === tMin
      ? W / 2
      : PAD.left + ((ms - tMin) / (tMax - tMin)) * (W - PAD.left - PAD.right);
  const y = (v: number) =>
    PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);

  const up = numV[numV.length - 1] >= numV[0];
  const hue = up ? GAIN : LOSS;
  const gradId = up ? "perf-wash-gain" : "perf-wash-loss";

  const pts = vals.map((p, i) => [x(t(p.date)), y(numV[i])] as const);
  const seg = (from: number, to: number) =>
    pts
      .slice(from, to + 1)
      .map(([px, py], i) => `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`)
      .join(" ");

  // Split the value line at the estimated -> observed boundary (shared point).
  const b = firstObservedIndex(vals);
  const estPath = b > 0 ? seg(0, Math.min(b, pts.length - 1)) : null;
  const obsPath = b < pts.length ? seg(b, pts.length - 1) : null;

  const baseline = H - PAD.bottom;
  const obsStart = b < pts.length ? b : 0;
  const areaPath =
    `${seg(obsStart, pts.length - 1)} ` +
    `L${pts[pts.length - 1][0].toFixed(1)},${baseline} ` +
    `L${pts[obsStart][0].toFixed(1)},${baseline} Z`;

  // Net-contributions step line: hold each level, step at the next flow date,
  // then run flat to today's right edge. Neutral muted ink (reference baseline).
  let cPath: string | null = null;
  if (contrib.length > 0) {
    const cx = contrib.map((p) => x(t(p.date)));
    const cy = numC.map((v) => y(v));
    const parts = [`M${cx[0].toFixed(1)},${cy[0].toFixed(1)}`];
    for (let i = 1; i < contrib.length; i++) {
      parts.push(`L${cx[i].toFixed(1)},${cy[i - 1].toFixed(1)}`);
      parts.push(`L${cx[i].toFixed(1)},${cy[i].toFixed(1)}`);
    }
    parts.push(`L${x(tMax).toFixed(1)},${cy[cy.length - 1].toFixed(1)}`);
    cPath = parts.join(" ");
  }

  const last = pts.length - 1;

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Portfolio value and net contributions, ${vals[0].date} to ${vals[last].date}`}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={hue} stopOpacity={0.08} />
            <stop offset="100%" stopColor={hue} stopOpacity={0} />
          </linearGradient>
        </defs>

        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={baseline}
          y2={baseline}
          stroke="#23262e"
          strokeWidth={1}
        />

        <path d={areaPath} fill={`url(#${gradId})`} />

        {/* net-contributions reference baseline (muted, stepped) */}
        {cPath && (
          <path
            d={cPath}
            fill="none"
            stroke="var(--color-ink-2)"
            strokeWidth={1.5}
            strokeDasharray="1 0"
            opacity={0.7}
          >
            <title>Net contributions (capital in − out)</title>
          </path>
        )}

        {/* reconstructed pre-go-live tail: same hue, dashed + faded */}
        {estPath && (
          <path
            d={estPath}
            fill="none"
            stroke={hue}
            strokeOpacity={0.45}
            strokeWidth={2}
            strokeDasharray="5 4"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}
        {/* observed value line: solid */}
        {obsPath && (
          <path
            d={obsPath}
            fill="none"
            stroke={hue}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}

        <circle
          cx={pts[last][0]}
          cy={pts[last][1]}
          r={4}
          fill={hue}
          stroke="#0e0f13"
          strokeWidth={2}
        />

        {/* endpoint value labels (masked for viewers) */}
        <text
          x={pts[last][0]}
          y={pts[last][1] - 8}
          textAnchor="end"
          className="fill-ink-2 text-[11px] tabular-nums"
        >
          {display(numV[last], masked)}
        </text>

        <text x={PAD.left} y={H - 6} textAnchor="start" className="fill-ink-3 text-[11px] tabular-nums">
          {shortDate(vals[0].date)}
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" className="fill-ink-3 text-[11px] tabular-nums">
          {shortDate(vals[last].date)}
        </text>

        {/* hover hit targets on value points */}
        {pts.map(([px, py], i) => (
          <circle key={i} cx={px} cy={py} r={10} fill="transparent">
            <title>{`${vals[i].date}: ${display(numV[i], masked)}${vals[i].estimated ? " (est.)" : ""}`}</title>
          </circle>
        ))}
      </svg>

      {/* legend — identity never by color alone */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-3">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4" style={{ background: hue }} /> Value (observed)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 opacity-45" style={{ background: hue, backgroundImage: "none" }} />
          Value (estimated, dashed)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-ink-2" /> Net contributions
        </span>
      </div>
    </div>
  );
}

export default function PerformanceSection({
  data,
  masked,
}: {
  // headline metrics per period; series are identical across periods.
  data: Record<Period, Performance>;
  masked: boolean;
}) {
  const [period, setPeriod] = useState<Period>("inception");
  const perf = data[period];

  return (
    <section aria-label="Performance">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="micro-label">Performance</h2>
        <div role="tablist" aria-label="Performance period" className="flex gap-1 rounded-lg bg-card p-0.5">
          {PERIODS.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={period === p.id}
              onClick={() => setPeriod(p.id)}
              className={`rounded-md px-2.5 py-1 text-[13px] transition-colors ${
                period === p.id
                  ? "bg-surface font-medium text-ink"
                  : "text-ink-2 hover:text-ink"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {perf.available ? (
        <Headline perf={perf} masked={masked} />
      ) : (
        <p className="rounded-lg bg-card px-4 py-6 text-center text-sm text-ink-2">
          Not enough history yet for this period.
        </p>
      )}

      <div className="mt-6">
        <Chart perf={perf} masked={masked} />
      </div>

      {perf.available && perf.caveats && perf.caveats.length > 0 && (
        <ul className="mt-3 space-y-0.5 text-xs text-ink-3">
          {perf.caveats.map((c, i) => (
            <li key={i}>• {c}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Headline({ perf, masked }: { perf: Performance; masked: boolean }) {
  const pnl = Number(perf.dollar_pnl_usd ?? 0);
  const pnlTone = pnl >= 0 ? "gain" : "loss";
  const pnlSign = pnl >= 0 ? "+" : "-";

  // Annualized is the headline only for since-inception (meaningful over the
  // full life); short windows lead with the cumulative period return, and the
  // annualized figure rides along as a small secondary value.
  const cumulative = perf.headline_metric === "cumulative";
  const primaryPct = cumulative ? perf.cumulative_return_pct : perf.mwr_annualized_pct;
  const primaryLabel = cumulative ? "Return (this period)" : "Money-weighted return";
  const secondary =
    cumulative && perf.mwr_annualized_pct != null
      ? `${signedPct(perf.mwr_annualized_pct)} annualized`
      : !cumulative && perf.cumulative_return_pct != null
        ? `${signedPct(perf.cumulative_return_pct)} cumulative`
        : undefined;
  const retTone = (primaryPct != null ? Number(primaryPct) : 0) >= 0 ? "gain" : "loss";

  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
      <Stat label="Dollar P&L" tone={pnlTone} big>
        {masked ? "•••" : `${pnlSign}${usd(Math.abs(pnl))}`}
      </Stat>
      <Stat label={primaryLabel} tone={retTone} sub={secondary}>
        {signedPct(primaryPct ?? null)}
      </Stat>
      <Stat label="Net contributions">
        {display(perf.net_contributions_usd ?? "0", masked)}
      </Stat>
      <Stat label="Time-weighted (est.)">{signedPct(perf.twr_pct ?? null)}</Stat>
    </div>
  );
}
