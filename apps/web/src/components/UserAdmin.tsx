"use client";

// Owner-only user administration panel. Both forms post to server actions
// that re-verify the owner role — this component being owner-rendered is
// cosmetic, not the security boundary.

import { useActionState } from "react";
import { addViewer, removeViewer, type UserAdminState } from "@/app/actions/users";
import type { UserRow } from "@/db";

const IDLE: UserAdminState = { ok: null, error: null };

function Messages({ state }: { state: UserAdminState }) {
  if (state.error)
    return <p className="mt-2 text-sm text-loss">{state.error}</p>;
  if (state.ok) return <p className="mt-2 text-sm text-gain">{state.ok}</p>;
  return null;
}

export default function UserAdmin({ users }: { users: UserRow[] }) {
  const [addState, addAction, addPending] = useActionState(addViewer, IDLE);
  const [removeState, removeAction, removePending] = useActionState(removeViewer, IDLE);

  return (
    <div className="flex flex-col gap-8">
      <section aria-label="Allowlisted users">
        <h2 className="micro-label mb-2">Allowlist ({users.length})</h2>
        <div className="rounded-xl border border-hairline bg-card">
          <ul>
            {users.map((u, i) => (
              <li
                key={u.id}
                className={`flex h-12 items-center justify-between gap-3 px-4 ${
                  i > 0 ? "border-t border-hairline" : ""
                }`}
              >
                <div className="flex items-center gap-3 overflow-hidden">
                  <span className="truncate text-sm text-ink">{u.email}</span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-[11px] ${
                      u.role === "owner"
                        ? "border-accent/50 text-accent"
                        : "border-hairline text-ink-2"
                    }`}
                  >
                    {u.role}
                  </span>
                  {u.role === "viewer" && (
                    <span className="text-[11px] text-ink-3">dollars masked</span>
                  )}
                </div>
                {u.role === "viewer" && (
                  <form action={removeAction}>
                    <input type="hidden" name="email" value={u.email} />
                    <button
                      type="submit"
                      disabled={removePending}
                      className="rounded-md border border-hairline px-2.5 py-1 text-xs text-ink-2 transition-colors hover:border-loss/60 hover:text-loss disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </form>
                )}
              </li>
            ))}
          </ul>
        </div>
        <Messages state={removeState} />
      </section>

      <section aria-label="Add viewer">
        <h2 className="micro-label mb-2">Add viewer</h2>
        <form action={addAction} className="flex max-w-md items-center gap-2">
          <input
            type="email"
            name="email"
            required
            placeholder="person@example.com"
            autoComplete="off"
            className="w-full rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-ink-3"
          />
          <button
            type="submit"
            disabled={addPending}
            className="whitespace-nowrap rounded-md border border-hairline bg-card px-3 py-1.5 text-sm text-ink transition-colors hover:border-accent/60 disabled:opacity-50"
          >
            {addPending ? "Adding…" : "Add viewer"}
          </button>
        </form>
        <Messages state={addState} />
        <p className="mt-3 max-w-md text-[13px] leading-relaxed text-ink-3">
          Viewers see holdings, weights and percent moves; every dollar amount and
          quantity is masked. New sign-ins also require the address to be listed as a
          Test user on the Google OAuth consent screen (the app is in Testing mode).
        </p>
      </section>
    </div>
  );
}
