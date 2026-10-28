import { agentRunThread } from "@/lib/usage";

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

// Reads the body once: whether this is an agent run and which thread it belongs to.
export async function readRun(req: Request): Promise<{ run: boolean; threadId: string | null }> {
  try {
    const body = await req.clone().json();
    return { run: body?.method === "agent/run", threadId: agentRunThread(body) };
  } catch {
    return { run: false, threadId: null };
  }
}

export async function isAgentRun(req: Request): Promise<boolean> {
  return (await readRun(req)).run;
}
