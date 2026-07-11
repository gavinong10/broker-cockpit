// Server component: hero figure (total value) + day change delta + cash.
// Dollar amounts respect masking; percent change is always real.
// Gain/loss color is polarity/status only and always paired with an
// explicit +/- sign, so it is never the sole carrier of direction.

import { display, pct, usd } from "@/lib/format";

export default function PortfolioHeader({
  totalValueUsd,
  dayChangeUsd,
  dayChangePct,
  cashUsd,
  masked,
}: {
  totalValueUsd: string;
  dayChangeUsd: string;
  dayChangePct: string;
  cashUsd: string;
  masked: boolean;
}) {
  const change = Number(dayChangeUsd);
  const up = change >= 0;
  const deltaColor = up ? "text-gain" : "text-loss";
  const sign = up ? "+" : "";

  return (
    <header>
      <p className="micro-label">Portfolio value</p>
      <p className="mt-1 text-[40px] font-semibold leading-tight tracking-tight text-ink">
        {display(totalValueUsd, masked)}
      </p>
      <p className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        <span
          className={`inline-flex items-baseline gap-1 rounded-full px-2 py-0.5 font-medium ${deltaColor} ${
            up ? "bg-gain/10" : "bg-loss/10"
          }`}
        >
          {masked ? "" : `${sign}${usd(dayChangeUsd)} `}
          ({sign}
          {pct(dayChangePct)}) today
        </span>
        <span className="text-ink-2">Cash {display(cashUsd, masked)}</span>
      </p>
    </header>
  );
}
