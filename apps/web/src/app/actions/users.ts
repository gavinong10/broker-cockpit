"use server";

// Owner-only user administration. The role check in each action is the
// security boundary — page-level gating and hidden nav tabs are cosmetic.
// Every mutation lands in audit_log with the acting owner as actor.

import { revalidatePath } from "next/cache";
import { requireOwnerAction } from "@/app/actions/util";
import { auditFromWeb, deleteViewer, insertViewer } from "@/db";
import { PERMISSION_DENIED_MESSAGE } from "@/lib/roles";
import { isValidEmail, normalizeEmail } from "@/lib/users";

export type UserAdminState = { ok: string | null; error: string | null };

export async function addViewer(
  _prev: UserAdminState,
  formData: FormData,
): Promise<UserAdminState> {
  const owner = await requireOwnerAction("user.add");
  if (!owner) return { ok: null, error: PERMISSION_DENIED_MESSAGE };

  const raw = String(formData.get("email") ?? "");
  if (!isValidEmail(raw)) return { ok: null, error: "That doesn't look like a valid email." };
  const email = normalizeEmail(raw);

  const inserted = await insertViewer(email);
  if (!inserted) return { ok: null, error: `${email} is already on the allowlist.` };

  await auditFromWeb(owner.email, "user.added", { email, role: "viewer" });
  revalidatePath("/admin");
  return {
    ok: `${email} added as viewer. Remember to also add them as a Test user on the Google OAuth consent screen, or Google will block their sign-in.`,
    error: null,
  };
}

export async function removeViewer(
  _prev: UserAdminState,
  formData: FormData,
): Promise<UserAdminState> {
  const owner = await requireOwnerAction("user.remove");
  if (!owner) return { ok: null, error: PERMISSION_DENIED_MESSAGE };

  const email = normalizeEmail(String(formData.get("email") ?? ""));
  if (email === normalizeEmail(owner.email)) {
    return { ok: null, error: "You can't remove yourself." };
  }

  // deleteViewer only ever deletes role='viewer' rows — owners are untouchable
  // at the SQL level even if a forged form names one.
  const removed = await deleteViewer(email);
  if (!removed) return { ok: null, error: `${email} is not a removable viewer.` };

  await auditFromWeb(owner.email, "user.removed", { email });
  revalidatePath("/admin");
  return { ok: `${email} removed. Their session will stop working within minutes (role is re-checked from the DB on each JWT refresh).`, error: null };
}
