"use client";

// Owner-only Robinhood session refresh. Click reveals an inline credentials
// form posting to the refreshRobinhood server action (which re-verifies the
// owner role server-side — rendering this only for owners is NOT the
// security boundary). All fields are controlled React state so the password
// survives a 428 needs_code round-trip; it lives only in component memory
// (no localStorage, no persistence) and is cleared as soon as the refresh
// succeeds or the form is closed.

import { useActionState, useState } from "react";
import { useRouter } from "next/navigation";
import { refreshRobinhood, type RhRefreshState } from "@/app/actions/rh-refresh";

const IDLE: RhRefreshState = { kind: "idle" };

function channelLabel(channel: string): string {
  if (channel === "sms") return "sent by SMS";
  if (channel === "email") return "sent by email";
  return "from your authenticator";
}

const inputCls =
  "w-full rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-950 " +
  "dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50";

export default function RhRefreshButton({
  defaultUsername,
}: {
  defaultUsername: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState(defaultUsername);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [state, formAction, pending] = useActionState(
    async (prev: RhRefreshState, formData: FormData) => {
      const next = await refreshRobinhood(prev, formData);
      if (next.kind === "ok") {
        // Success: drop the credentials immediately and pull fresh data.
        setPassword("");
        setCode("");
        setOpen(false);
        router.refresh();
      }
      return next;
    },
    IDLE,
  );

  const close = () => {
    setOpen(false);
    setPassword("");
    setCode("");
  };

  if (!open) {
    return (
      <span className="flex items-center gap-2">
        {state.kind === "ok" && (
          <span className="text-sm text-[#006300] dark:text-[#0ca30c]" role="status">
            Session refreshed
            {state.syncedPositions !== null &&
              ` — synced ${state.syncedPositions} positions`}
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-md border border-zinc-300 px-3 py-1 text-sm text-zinc-950 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-50 dark:hover:bg-zinc-900"
        >
          Refresh Robinhood session
        </button>
      </span>
    );
  }

  return (
    <form
      action={formAction}
      className="flex w-full max-w-sm flex-col gap-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800"
    >
      <p className="text-sm font-medium text-zinc-950 dark:text-zinc-50">
        Refresh Robinhood session
      </p>
      <label className="text-xs text-zinc-500 dark:text-zinc-400">
        Username
        <input
          name="username"
          type="text"
          autoComplete="off"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
          className={inputCls}
        />
      </label>
      <label className="text-xs text-zinc-500 dark:text-zinc-400">
        Password
        <input
          name="password"
          type="password"
          autoComplete="off"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          className={inputCls}
        />
      </label>
      {state.kind === "needs_code" && (
        <label className="text-xs text-zinc-500 dark:text-zinc-400">
          Verification code ({channelLabel(state.channel)})
          <input
            name="code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className={inputCls}
          />
          <span className="mt-0.5 block text-[11px] text-zinc-400 dark:text-zinc-500">
            Enter the code {channelLabel(state.channel)}. Submitting without a
            code triggers a fresh challenge (any earlier code stops working).
          </span>
        </label>
      )}
      {pending && (
        <p className="text-sm text-amber-700 dark:text-amber-300" role="status">
          Requesting… check your phone for a Robinhood approval prompt (can
          take up to 2 minutes).
        </p>
      )}
      {!pending && state.kind === "error" && (
        <p className="text-sm text-red-700 dark:text-red-300" role="alert">
          {state.message}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending}
          className="rounded-md border border-zinc-300 px-3 py-1 text-sm text-zinc-950 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-50 dark:hover:bg-zinc-900"
        >
          {pending ? "Refreshing…" : "Refresh"}
        </button>
        <button
          type="button"
          onClick={close}
          disabled={pending}
          className="rounded-md px-3 py-1 text-sm text-zinc-500 hover:text-zinc-950 disabled:opacity-50 dark:text-zinc-400 dark:hover:text-zinc-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
