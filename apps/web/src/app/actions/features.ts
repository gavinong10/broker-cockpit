"use server";

// Feature-factory server actions. EVERY action re-verifies owner server-side —
// the hidden tab is cosmetic. These proxy to the worker, which is the only
// component that can reach the host build runner (over a forced-command SSH key).

import { auth } from "@/auth";
import { workerPost } from "@/lib/worker";

async function requireOwner(): Promise<{ ok: true; email: string } | { ok: false }> {
  const session = await auth();
  const u = session?.user as { role?: string; email?: string } | undefined;
  if (u?.role !== "owner") return { ok: false };
  return { ok: true, email: u.email ?? "owner" };
}

export type ActionResult = { ok: boolean; message: string };

export async function createFeature(
  _prev: ActionResult,
  formData: FormData,
): Promise<ActionResult> {
  const owner = await requireOwner();
  if (!owner.ok) return { ok: false, message: "Owner only." };
  const prompt = String(formData.get("prompt") ?? "").trim();
  const model = String(formData.get("model") ?? "").trim() || undefined;
  if (prompt.length < 20) return { ok: false, message: "Describe the feature in a bit more detail." };
  try {
    // The worker answers 202 immediately (async build start) — this call is
    // create + spawn only; the build itself is polled via the feature list.
    const { status, body } = await workerPost(
      "/internal/features",
      { prompt, model, actor: owner.email },
      { timeoutMs: 60_000 },
    );
    if (status !== 200 && status !== 202) {
      const b = body as { error?: string };
      return { ok: false, message: b?.error ?? `Worker returned ${status}` };
    }
    const b = body as { slug: string };
    return {
      ok: true,
      message: `Build "${b.slug}" started — this takes several minutes; the list below updates as it runs.`,
    };
  } catch {
    return { ok: false, message: "Could not reach the build runner." };
  }
}

export async function factoryPause(paused: boolean): Promise<ActionResult> {
  const owner = await requireOwner();
  if (!owner.ok) return { ok: false, message: "Owner only." };
  try {
    const { status, body } = await workerPost(
      `/internal/features/runner/${paused ? "pause" : "resume"}`,
      { actor: owner.email },
      { timeoutMs: 30_000 },
    );
    if (status !== 200) {
      const b = body as { error?: string };
      return { ok: false, message: b?.error ?? `Worker returned ${status}` };
    }
    return { ok: true, message: paused ? "Factory paused." : "Factory resumed." };
  } catch {
    return { ok: false, message: "Could not reach the build runner." };
  }
}

export async function featureAction(
  slug: string,
  verb: "accept" | "revert" | "sync" | "kill",
): Promise<ActionResult> {
  const owner = await requireOwner();
  if (!owner.ok) return { ok: false, message: "Owner only." };
  try {
    const { status, body } = await workerPost(
      `/internal/features/${encodeURIComponent(slug)}/${verb}`,
      { actor: owner.email },
      { timeoutMs: 900_000 },
    );
    if (status !== 200) {
      const b = body as { error?: string };
      return { ok: false, message: b?.error ?? `Worker returned ${status}` };
    }
    if (verb === "accept") {
      // The merge is committed and pushed; the container rebuild runs detached
      // on the host, so the site (including this page) restarts underneath us.
      return { ok: true, message: "Accepted — redeploying; the site may blip for about a minute." };
    }
    const b = body as { status?: string };
    return { ok: true, message: b?.status ?? "done" };
  } catch {
    // Builds run host-side regardless of this call's fate — never claim a
    // timeout killed anything; the list poll shows the authoritative status.
    return { ok: false, message: "Runner call failed — refresh to see the current status." };
  }
}
