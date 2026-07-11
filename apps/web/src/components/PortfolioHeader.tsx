// Server component: hero figure (total value) + day change delta + cash.
// Dollar amounts respect masking; percent change is always real.

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
  const deltaColor = up
    ? "text-[#006300] dark:text-[#0ca30c]"
    : "text-[#d03b3b]";
  const sign = up ? "+" : "";

  return (
    <header>
      <p className="text-sm text-zinc-500 dark:text-zinc-400">Portfolio value</p>
      <p className="text-5xl font-semibold text-zinc-950 dark:text-zinc-50">
        {display(totalValueUsd, masked)}
      </p>
      <p className={`mt-1 text-sm font-medium ${deltaColor}`}>
        {masked ? "" : `${sign}${usd(dayChangeUsd)} `}
        ({sign}
        {pct(dayChangePct)}) today
      </p>
      <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
        Cash {display(cashUsd, masked)}
      </p>
    </header>
  );
}
