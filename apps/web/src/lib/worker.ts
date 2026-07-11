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
 * JSON POST to the worker that never throws on HTTP errors: returns
 * {status, body} so callers can branch on the worker's status contract
 * (e.g. 428 needs_code). Long default timeout: RH device approval can
 * take ~2 minutes. Server-side only (internal token, docker DNS).
 */
export async function workerPost(
  path: string,
  payload: unknown,
  { timeoutMs = 155_000 }: { timeoutMs?: number } = {},
): Promise<{ status: number; body: unknown }> {
  const res = await fetch(`http://worker:8000${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Token": process.env.INTERNAL_API_TOKEN!,
    },
    body: JSON.stringify(payload),
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
  });
  const body = await res.json().catch(() => null);
  return { status: res.status, body };
}

export async function workerDelete(
  path: string,
): Promise<{ status: number; body: unknown }> {
  const res = await fetch(`http://worker:8000${path}`, {
    method: "DELETE",
    headers: { "X-Internal-Token": process.env.INTERNAL_API_TOKEN! },
    cache: "no-store",
  });
  const body = await res.json().catch(() => null);
  return { status: res.status, body };
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
