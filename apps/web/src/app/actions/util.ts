// Shared owner gate for mutating server actions. NOT a "use server" file on
// purpose: this is a plain server-side helper, not an action endpoint.
//
// Every mutating server action must call requireOwnerAction() FIRST and bail
// with PERMISSION_DENIED_MESSAGE (from @/lib/roles) when it returns null.
// Viewers see (and can click) every button in the UI — rendering is never the
// security boundary; this check is. It always uses the REAL session role via
// auth(), never the effective/preview view, so an owner in viewer-preview can
// still act. Refused attempts are audit-logged as perm.denied.

import { auth } from "@/auth";
import { auditFromWeb } from "@/db";

/** Server-side owner check for actions: resolves the acting owner's email,
 * or null (after audit-logging the refusal) for viewers, missing sessions,
 * and sessions without an email. `action` names the attempted mutation in
 * the audit trail, e.g. "journal.add" or "feature.accept". */
export async function requireOwnerAction(
  action: string,
): Promise<{ email: string } | null> {
  const session = await auth();
  const user = session?.user as
    | { role?: "owner" | "viewer" | null; email?: string | null }
    | undefined;
  if (user?.role !== "owner" || !user.email) {
    await auditFromWeb(user?.email ?? "unknown", "perm.denied", { action });
    return null;
  }
  return { email: user.email };
}
