import { auth } from "@/auth";

// Owner-only proxy for a feature's diff text (the worker returns plain text,
// so we fetch it directly rather than via the JSON helper). The worker holds
// the internal token; the browser never sees it. Non-owners get 403.
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const session = await auth();
  const role = (session?.user as { role?: string } | undefined)?.role;
  if (role !== "owner") {
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
