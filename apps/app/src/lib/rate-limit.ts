// In-memory sliding window per client. The public port sits behind the gate
// container, which overwrites X-Real-IP with the peer address it trusts.
const WINDOW_MS = Number(process.env.RATE_LIMIT_WINDOW_MS) || 60_000;
const MAX_RUNS = Number(process.env.RATE_LIMIT_MAX) || 10;
const hits = new Map<string, number[]>();

export function clientKey(headers: Headers): string {
  return headers.get("x-real-ip")?.trim() || "unknown";
}

export function isRateLimited(key: string, now = Date.now()): boolean {
  const recent = (hits.get(key) ?? []).filter((t) => t > now - WINDOW_MS);
  recent.push(now);
  hits.set(key, recent);
  if (hits.size > 10_000) {
    for (const [k, times] of hits) if (times.every((t) => t <= now - WINDOW_MS)) hits.delete(k);
  }
  return recent.length > MAX_RUNS;
}

export async function isAgentRun(req: Request): Promise<boolean> {
  try {
    const body = await req.clone().json();
    return body?.method === "agent/run";
  } catch {
    return false;
  }
}
