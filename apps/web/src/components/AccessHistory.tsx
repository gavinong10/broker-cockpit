// Owner-only access history, rendered inside the already-gated /admin page.
// Server component: <details> expansion needs no client JS.

import { formatChicago, type AccessEvent, type UserAccess } from "@/lib/access";

function EventLine({ e }: { e: AccessEvent }) {
  const rejected = e.category === "auth.rejected";
  return (
    <li className="flex items-center justify-between py-1 text-[13px]">
      <span className={rejected ? "text-loss" : "text-ink-2"}>
        {rejected ? "rejected attempt" : "sign-in"}
      </span>
      <time dateTime={e.at} className="text-ink-3">
        {formatChicago(e.at)}
      </time>
    </li>
  );
}

export default function AccessHistory({
  byUser,
  other,
}: {
  byUser: [string, UserAccess][];
  other: AccessEvent[];
}) {
  return (
    <section aria-label="Access history">
      <h2 className="micro-label mb-2">Access history (last 30 days)</h2>
      <div className="rounded-xl border border-hairline bg-card">
        <ul>
          {byUser.map(([email, u], i) => (
            <li key={email} className={i > 0 ? "border-t border-hairline" : ""}>
              <details>
                <summary className="flex h-12 cursor-pointer list-none items-center justify-between gap-3 px-4 [&::-webkit-details-marker]:hidden">
                  <span className="truncate text-sm text-ink">{email}</span>
                  <span className="shrink-0 text-[13px] text-ink-2">
                    {u.lastLoginAt === null ? (
                      <span className="text-ink-3">never signed in</span>
                    ) : (
                      <>
                        last {formatChicago(u.lastLoginAt)}
                        <span className="text-ink-3"> · {u.count30d} in 30d</span>
                      </>
                    )}
                  </span>
                </summary>
                {u.recent.length > 0 && (
                  <ul className="border-t border-hairline bg-surface/50 px-4 py-2 pl-8">
                    {u.recent.map((e, j) => (
                      <EventLine key={j} e={e} />
                    ))}
                  </ul>
                )}
              </details>
            </li>
          ))}
        </ul>
      </div>
      {other.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] text-ink-3">
            Events from non-allowlisted accounts ({other.length}) — rejected
            attempts and removed users
          </summary>
          <ul className="mt-1 rounded-xl border border-hairline bg-card px-4 py-2">
            {other.slice(0, 20).map((e, i) => (
              <li key={i} className="flex items-center justify-between py-1 text-[13px]">
                <span className={e.category === "auth.rejected" ? "text-loss" : "text-ink-2"}>
                  {e.actor} — {e.category === "auth.rejected" ? "rejected" : "sign-in"}
                </span>
                <time dateTime={e.at} className="text-ink-3">
                  {formatChicago(e.at)}
                </time>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
