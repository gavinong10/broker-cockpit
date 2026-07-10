export async function workerFetch(path: string, init: RequestInit = {}) {
  const res = await fetch(`http://worker:8000${path}`, {
    ...init,
    headers: { ...init.headers, "X-Internal-Token": process.env.INTERNAL_API_TOKEN! },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`worker ${path}: ${res.status}`);
  return res.json();
}
