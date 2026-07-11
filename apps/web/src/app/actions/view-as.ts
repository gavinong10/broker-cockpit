"use server";

// Owner-only "view as viewer" preview toggle. Entering requires the REAL
// owner role; exiting is always allowed (deleting the cookie is harmless).
// The cookie is presentation-only: effectiveView() can only downgrade.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { requireOwnerAction } from "@/app/actions/util";
import { VIEWER_PREVIEW_COOKIE } from "@/lib/viewerContext";

export async function enterViewerPreview() {
  const owner = await requireOwnerAction("viewer_preview.enter");
  if (!owner) return;
  (await cookies()).set(VIEWER_PREVIEW_COOKIE, "1", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });
  redirect("/");
}

export async function exitViewerPreview() {
  (await cookies()).delete(VIEWER_PREVIEW_COOKIE);
  redirect("/");
}
