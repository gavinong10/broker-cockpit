"use server";

// Owner-only journal mutations. The role check here is the security
// boundary; page-level gating is cosmetic. The journal is owner-only by
// design: notes and target/stop levels are free-form dollars that masking
// cannot reliably scrub, so viewers never receive entry data at all.

import { revalidatePath } from "next/cache";
import { auth } from "@/auth";
import { workerDelete, workerPost } from "@/lib/worker";

export type JournalFormState = { ok: boolean; error: string | null };

async function requireOwner(): Promise<{ email: string } | null> {
  const session = await auth();
  const user = session?.user as
    | { role?: "owner" | "viewer" | null; email?: string | null }
    | undefined;
  if (user?.role !== "owner" || !user.email) return null;
  return { email: user.email };
}

export async function addJournalEntry(
  _prev: JournalFormState,
  formData: FormData,
): Promise<JournalFormState> {
  const owner = await requireOwner();
  if (!owner) return { ok: false, error: "Not authorized — owner only." };

  const symbol = String(formData.get("symbol") ?? "").trim();
  const tag = String(formData.get("tag") ?? "").trim();
  const note = String(formData.get("note") ?? "").trim();
  if (!symbol || !tag || !note) {
    return { ok: false, error: "Symbol, tag and note are all required." };
  }
  const opt = (name: string) => {
    const v = String(formData.get(name) ?? "").trim();
    return v === "" ? null : v;
  };
  const confidenceRaw = opt("confidence");

  const { status, body } = await workerPost("/internal/journal", {
    symbol,
    tag,
    note,
    actor: owner.email,
    target_usd: opt("target_usd"),
    stop_usd: opt("stop_usd"),
    confidence: confidenceRaw === null ? null : Number(confidenceRaw),
  });
  if (status !== 200) {
    const detail =
      (body as { detail?: unknown } | null)?.detail ?? `worker returned ${status}`;
    return { ok: false, error: `Could not save: ${typeof detail === "string" ? detail : status}` };
  }
  revalidatePath(`/positions/${encodeURIComponent(symbol)}`);
  revalidatePath("/journal");
  return { ok: true, error: null };
}

export async function deleteJournalEntry(
  _prev: JournalFormState,
  formData: FormData,
): Promise<JournalFormState> {
  const owner = await requireOwner();
  if (!owner) return { ok: false, error: "Not authorized — owner only." };
  const id = Number(formData.get("id"));
  if (!Number.isInteger(id) || id <= 0) return { ok: false, error: "Bad entry id." };
  const { status } = await workerDelete(`/internal/journal/${id}`);
  if (status !== 200) return { ok: false, error: `Delete failed (${status}).` };
  const symbol = String(formData.get("symbol") ?? "");
  if (symbol) revalidatePath(`/positions/${encodeURIComponent(symbol)}`);
  revalidatePath("/journal");
  return { ok: true, error: null };
}
