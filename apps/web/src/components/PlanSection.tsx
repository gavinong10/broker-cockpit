// Basket Plan section: pending purchases graded against the plan every sync
// cycle. Server component — all data arrives precomputed from the worker's
// /internal/baskets/{slug}/plan view; charts are static SVG (quiet theme).

import { display } from "@/lib/format";
import {
  fillSlippagePct,
  legChip,
  payoffPath,
  sparklinePoints,
  structureSummary,
  type ChipTone,
  type PlanLeg,
  type PlanView,
} from "@/lib/plans";

const CHIP_CLASSES: Record<ChipTone, string> = {
  gain: "border-gain/40 text-gain",
  amber: "border-[#e8a13d]/50 text-[#e8a13d]",
  loss: "border-loss/50 text-loss",
  muted: "border-hairline text-ink-3",
  accent: "border-accent/50 text-accent",
};

function Chip({ text, tone }: { text: string; tone: ChipTone }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.08em] ${CHIP_CLASSES[tone]}`}
    >
      {text}
    </span>
  );
}

function Sparkline({ leg }: { leg: PlanLeg }) {
  const spark = sparklinePoints(leg.marks, Number(leg.planned_net_debit));
  if (!spark) return null;
  return (
    <svg
      width="120"
      height="32"
      viewBox="0 0 120 32"
      aria-label={`Live cost history for ${leg.label}`}
      className="shrink-0"
    >
      <line
        x1="0"
        x2="120"
        y1={spark.plannedY}
        y2={spark.plannedY}
        stroke="var(--color-ink-3)"
        strokeDasharray="3 3"
        strokeWidth="1"
      />
      <polyline
        points={spark.points}
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function LegRow({ leg, masked }: { leg: PlanLeg; masked: boolean }) {
  const chip = legChip(leg);
  const slippage = fillSlippagePct(leg);
  const delta = leg.last_quote_delta_pct;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-hairline bg-card p-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-sm font-medium text-ink">{leg.label}</span>
        <Chip text={chip.text} tone={chip.tone} />
        <span className="text-xs text-ink-3">{structureSummary(leg)}</span>
        <span className="ml-auto">
          <Sparkline leg={leg} />
        </span>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[13px] tabular-nums text-ink-2">
        <span>
          plan {display(leg.planned_net_debit, masked)}
          {leg.last_quote_net !== null && (
            <>
              {" · live "}
              {display(leg.last_quote_net, masked)}
              {delta !== null && (
                <span
                  className={
                    Number(delta) <= 0 ? "text-gain" : "text-[#e8a13d]"
                  }
                >
                  {" "}
                  ({Number(delta) > 0 ? "+" : ""}
                  {delta}%)
                </span>
              )}
            </>
          )}
        </span>
        <span>tolerance +{Number(leg.tolerance_pct)}%</span>
        {leg.breakeven_underlying !== null && (
          <span>BE {display(leg.breakeven_underlying, masked)}</span>
        )}
        {leg.max_value_usd !== null && (
          <span>max {display(leg.max_value_usd, masked)}</span>
        )}
        {slippage !== null && (
          <span className={slippage <= 0 ? "text-gain" : "text-[#e8a13d]"}>
            filled {display(leg.filled_net_debit!, masked)} (
            {slippage > 0 ? "+" : ""}
            {slippage.toFixed(1)}% vs plan)
          </span>
        )}
        {leg.last_quoted_at !== null && (
          <span className="text-ink-3">
            checked {new Date(leg.last_quoted_at).toLocaleString()}
          </span>
        )}
      </div>
      {leg.thesis_note !== null && (
        <p className="text-[13px] leading-5 text-ink-3">{leg.thesis_note}</p>
      )}
    </li>
  );
}

function PayoffCurve({ plan, masked }: { plan: PlanView; masked: boolean }) {
  const geom = payoffPath(plan.payoff_curve);
  if (!geom) return null;
  const first = plan.payoff_curve[0];
  const last = plan.payoff_curve[plan.payoff_curve.length - 1];
  const maxPnl = Math.max(...plan.payoff_curve.map((p) => Number(p.pnl_usd)));
  return (
    <div className="rounded-lg border border-hairline bg-card p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="micro-label">Payoff at expiry (uniform move)</h3>
        <span className="text-xs tabular-nums text-ink-3">
          {first.move_pct}% … {last.move_pct}% · max{" "}
          {masked ? "•••" : `$${maxPnl.toLocaleString()}`}
        </span>
      </div>
      <svg
        viewBox="0 0 560 160"
        className="mt-2 w-full"
        role="img"
        aria-label="Plan payoff curve versus uniform underlying move"
      >
        <line
          x1="0"
          x2="560"
          y1={geom.zeroY}
          y2={geom.zeroY}
          stroke="var(--color-hairline)"
          strokeWidth="1"
        />
        <line
          x1={`${(geom.xZeroPct / 100) * 560}`}
          x2={`${(geom.xZeroPct / 100) * 560}`}
          y1="0"
          y2="160"
          stroke="var(--color-hairline)"
          strokeDasharray="3 3"
          strokeWidth="1"
        />
        <path
          d={geom.path}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
        />
      </svg>
      {plan.curve_excluded.length > 0 && (
        <p className="mt-1 text-xs text-ink-3">
          Not modeled: {plan.curve_excluded.join(", ")}
        </p>
      )}
    </div>
  );
}

export default function PlanSection({
  plan,
  masked,
}: {
  plan: PlanView;
  masked: boolean;
}) {
  if (plan.legs.length === 0) return null;
  const held = plan.legs.filter((l) => l.status === "held").length;
  const pending = plan.legs.length - held;
  return (
    <section
      aria-label="Basket plan"
      className="flex flex-col gap-4"
    >
      <div className="flex items-baseline gap-3">
        <h2 className="micro-label">Plan</h2>
        <span className="text-xs tabular-nums text-ink-3">
          planned {display(plan.planned_total_usd, masked)} · {held} filled ·{" "}
          {pending} pending
        </span>
      </div>
      <PayoffCurve plan={plan} masked={masked} />
      <ul className="flex flex-col gap-2.5">
        {plan.legs.map((leg) => (
          <LegRow key={leg.label} leg={leg} masked={masked} />
        ))}
      </ul>
    </section>
  );
}
