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
  "mt-1 w-full rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-sm text-ink " +
  "placeholder:text-ink-3";

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
          <span className="text-sm text-gain" role="status">
            Session refreshed
            {state.syncedPositions !== null &&
              ` — synced ${state.syncedPositions} positions`}
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-full border border-hairline px-3.5 py-1.5 text-[13px] text-ink-2 transition-colors hover:bg-hover hover:text-ink"
        >
          Refresh Robinhood session
        </button>
      </span>
    );
  }

  return (
    <form
      action={formAction}
      className="flex w-full max-w-sm flex-col gap-2 rounded-xl border border-hairline bg-card p-5"
    >
      <p className="text-sm font-medium text-ink">
        Refresh Robinhood session
      </p>
      <label className="micro-label block">
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
      <label className="micro-label block">
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
        <label className="micro-label block">
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
          <span className="mt-1 block text-[11px] normal-case tracking-normal text-ink-3">
            Enter the code {channelLabel(state.channel)}. Submitting without a
            code triggers a fresh challenge (any earlier code stops working).
          </span>
        </label>
      )}
      {pending && (
        <p className="text-sm text-amber-400" role="status">
          Requesting… check your phone for a Robinhood approval prompt (can
          take up to 2 minutes).
        </p>
      )}
      {!pending && state.kind === "error" && (
        <p className="text-sm text-loss" role="alert">
          {state.message}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending}
          className="rounded-full border border-hairline px-3.5 py-1.5 text-[13px] font-medium text-ink transition-colors hover:bg-hover disabled:opacity-50"
        >
          {pending ? "Refreshing…" : "Refresh"}
        </button>
        <button
          type="button"
          onClick={close}
          disabled={pending}
          className="rounded-full px-3.5 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
