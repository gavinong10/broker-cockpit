export async function workerFetch(path: string, init: RequestInit = {}) {
  const res = await fetch(`http://worker:8000${path}`, {
    ...init,
    headers: { ...init.headers, "X-Internal-Token": process.env.INTERNAL_API_TOKEN! },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`worker ${path}: ${res.status}`);
  return res.json();
}

/**
 * Like workerFetch but never throws on HTTP errors: returns {status, body}
 * so callers can render error states (e.g. 502 {"error":"rh_auth"}) instead
 * of crashing the page. Body is null when the response is not JSON.
 */
export async function workerFetchRaw(
  path: string,
): Promise<{ status: number; body: unknown }> {
  const res = await fetch(`http://worker:8000${path}`, {
    headers: { "X-Internal-Token": process.env.INTERNAL_API_TOKEN! },
    cache: "no-store",
  });
  const body = await res.json().catch(() => null);
  return { status: res.status, body };
}
