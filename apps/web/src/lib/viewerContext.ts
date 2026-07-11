import { cookies } from "next/headers";
import { auth } from "@/auth";
import { effectiveView, type Role, type ViewerView } from "./roles";

export const VIEWER_PREVIEW_COOKIE = "viewer-preview";

export type ViewerContext = ViewerView & { email: string; realRole: Role };

/** Session + owner's optional viewer-preview cookie, resolved to what this
 * request should RENDER as. Server actions must keep checking the REAL role
 * (via auth()) — this context governs presentation only, and effectiveView
 * guarantees the cookie can only ever downgrade an owner. */
export async function getViewerContext(): Promise<ViewerContext> {
  const [session, store] = await Promise.all([auth(), cookies()]);
  const u = session?.user as
    | { role?: Role; mask_amounts?: boolean }
    | undefined;
  const realRole = u?.role ?? null;
  const view = effectiveView(
    realRole,
    u?.mask_amounts,
    store.get(VIEWER_PREVIEW_COOKIE)?.value === "1",
  );
  return { email: session?.user?.email ?? "unknown", realRole, ...view };
}
