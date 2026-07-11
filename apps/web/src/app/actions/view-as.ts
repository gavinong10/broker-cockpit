"use server";

// Owner-only "view as viewer" preview toggle. Entering requires the REAL
// owner role; exiting is always allowed (deleting the cookie is harmless).
// The cookie is presentation-only: effectiveView() can only downgrade.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { VIEWER_PREVIEW_COOKIE } from "@/lib/viewerContext";

export async function enterViewerPreview() {
  const session = await auth();
  const role = (session?.user as { role?: string } | undefined)?.role;
  if (role !== "owner") return;
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
