"use server";

// Server action: owner-triggered Robinhood session refresh via the worker.
// The role check here is the security boundary — the button only being
// rendered for owners is cosmetic; anyone can POST a server action.
// The password passes straight through to the worker and is never logged
// or persisted anywhere in the web tier.

import { auth } from "@/auth";
import { workerPost } from "@/lib/worker";

export type RhRefreshState =
  | { kind: "idle" }
  | { kind: "ok"; syncedPositions: number | null }
  | { kind: "needs_code"; channel: string }
  | { kind: "error"; message: string };

/** Best-effort position count out of the worker's sync summary (shape is
 * loose in the contract: 200 {"status":"ok","expires_in":N,"sync":{...}}). */
function syncedPositions(sync: unknown): number | null {
  if (typeof sync !== "object" || sync === null) return null;
  const s = sync as Record<string, unknown>;
  // Worker's sync summary is a SyncResult: {equity_positions, option_positions, ...}
  const eq = typeof s.equity_positions === "number" ? s.equity_positions : null;
  const opt = typeof s.option_positions === "number" ? s.option_positions : null;
  if (eq === null && opt === null) return null;
  return (eq ?? 0) + (opt ?? 0);
}

export async function refreshRobinhood(
  _prevState: RhRefreshState,
  formData: FormData,
): Promise<RhRefreshState> {
  const session = await auth();
  const user = session?.user as { role?: "owner" | "viewer" | null } | undefined;
  if (user?.role !== "owner") {
    return { kind: "error", message: "Not authorized — owner only." };
  }

  const username = String(formData.get("username") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const code = String(formData.get("code") ?? "").trim();
  if (!username || !password) {
    return { kind: "error", message: "Username and password are required." };
  }

  let status: number;
  let body: unknown;
  try {
    ({ status, body } = await workerPost(
      "/internal/rh/refresh",
      {
        username,
        password,
        ...(code ? { code } : {}),
        actor: session?.user?.email ?? "unknown",
      },
      { timeoutMs: 155_000 }, // device approval can take ~2 min
    ));
  } catch {
    // Network failure or our own 155s timeout. Never include the error
    // object in anything rendered/logged — it can embed the request body.
    return {
      kind: "error",
      message: "Refresh request failed or timed out — try again.",
    };
  }

  const b = (body ?? {}) as {
    status?: string;
    channel?: string;
    detail?: unknown;
    sync?: unknown;
  };

  if (status === 200 && b.status === "ok") {
    return { kind: "ok", syncedPositions: syncedPositions(b.sync) };
  }
  if (status === 428) {
    return { kind: "needs_code", channel: b.channel ?? "code" };
  }
  if (status === 409) {
    return {
      kind: "error",
      message: "A refresh is already in progress — try again shortly.",
    };
  }
  if (status === 504) {
    return {
      kind: "error",
      message:
        "Timed out waiting for Robinhood (approval not confirmed in time) — try again.",
    };
  }
  const detail = typeof b.detail === "string" ? b.detail : `worker returned ${status}`;
  return { kind: "error", message: `Refresh failed: ${detail}` };
}
