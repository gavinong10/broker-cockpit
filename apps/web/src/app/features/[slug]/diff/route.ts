import { auth } from "@/auth";
import { canRead, type Role } from "@/lib/roles";

// Read proxy for a feature's diff text (the worker returns plain text, so we
// fetch it directly rather than via the JSON helper). Diffs are readable by
// every signed-in role — viewers see the factory read-only; only the
// mutating actions are owner-gated. The worker holds the internal token;
// the browser never sees it. Revoked/anonymous sessions get 403.
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const session = await auth();
  const role = (session?.user as { role?: Role } | undefined)?.role ?? null;
  if (!canRead(role)) {
    return new Response("Forbidden", { status: 403 });
  }
  const { slug } = await params;
  const res = await fetch(
    `http://worker:8000/internal/features/${encodeURIComponent(slug)}/diff`,
    { headers: { "X-Internal-Token": process.env.INTERNAL_API_TOKEN! }, cache: "no-store" },
  );
  const text = await res.text();
  return new Response(text, {
    status: res.ok ? 200 : 502,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}
