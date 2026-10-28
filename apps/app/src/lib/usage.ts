import { after } from "next/server";

// The agent only sees the gate's internal network, so the visitor IP is known here only.
// Each question is reported to the agent, which hashes the IP and stores the event.
// Same window as rate-limit.ts.
const WINDOW_MS = Number(process.env.RATE_LIMIT_WINDOW_MS) || 60_000;
const lastLimited = new Map<string, number>();

export function agentRunThread(envelope: unknown): string | null {
  const threadId = (envelope as { body?: { threadId?: unknown } } | null)?.body?.threadId;
  return typeof threadId === "string" ? threadId.slice(0, 200) : null;
}

// One limited event per key per rate-limit window, so a flood does not become a flood of records.
export function shouldSendLimited(key: string, now = Date.now()): boolean {
  const last = lastLimited.get(key);
  if (last !== undefined && now - last < WINDOW_MS) return false;
  lastLimited.set(key, now);
  if (lastLimited.size > 10_000) {
    for (const [k, t] of lastLimited) if (now - t >= WINDOW_MS) lastLimited.delete(k);
  }
  return true;
}

// For tests: the keys the pruning kept.
export function limitedKeys(): string[] {
  return [...lastLimited.keys()];
}

export function recordQuestion({ ip, threadId, limited }: { ip: string; threadId: string | null; limited: boolean }): void {
  const token = process.env.ADMIN_TOKEN ?? "";
  if (token.length < 32) return;
  const base = process.env.LANGGRAPH_DEPLOYMENT_URL ?? "http://localhost:8123";
  try {
    // Runs after the response is sent; a failure here must never reach the visitor.
    after(async () => {
      try {
        await fetch(`${base}/admin/usage/question`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-admin-token": token },
          body: JSON.stringify({ ip, thread_id: threadId, limited }),
          signal: AbortSignal.timeout(2000),
        });
      } catch {
        // Usage stats are best effort.
      }
    });
  } catch {
    // after() throws outside a request scope.
  }
}
