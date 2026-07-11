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
    const { status, body } = await workerPost(
      "/internal/features",
      { prompt, model, actor: owner.email },
      { timeoutMs: 60_000 },
    );
    if (status !== 200) {
      const b = body as { error?: string };
      return { ok: false, message: b?.error ?? `Worker returned ${status}` };
    }
    const b = body as { slug: string };
    return { ok: true, message: `Building "${b.slug}"… this can take several minutes.` };
  } catch {
    return { ok: false, message: "Could not reach the build runner." };
  }
}

export async function featureAction(
  slug: string,
  verb: "accept" | "revert" | "sync",
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
    const b = body as { status?: string };
    return { ok: true, message: b?.status ?? "done" };
  } catch {
    return { ok: false, message: "Runner call failed or timed out." };
  }
}
