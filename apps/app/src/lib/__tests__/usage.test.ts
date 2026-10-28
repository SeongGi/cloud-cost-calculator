import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const afterMock = vi.hoisted(() => vi.fn());
vi.mock("next/server", () => ({ after: afterMock }));

import { agentRunThread, recordQuestion, shouldSendLimited } from "../usage";

const TOKEN = "t".repeat(32);

describe("agentRunThread", () => {
  it("reads body.threadId", () => {
    expect(agentRunThread({ method: "agent/run", body: { threadId: "th-1" } })).toBe("th-1");
  });

  it("cuts to 200 chars", () => {
    expect(agentRunThread({ body: { threadId: "x".repeat(300) } })).toBe("x".repeat(200));
  });

  it("returns null when missing or not a string", () => {
    expect(agentRunThread(null)).toBeNull();
    expect(agentRunThread(undefined)).toBeNull();
    expect(agentRunThread("text")).toBeNull();
    expect(agentRunThread({})).toBeNull();
    expect(agentRunThread({ body: null })).toBeNull();
    expect(agentRunThread({ body: {} })).toBeNull();
    expect(agentRunThread({ body: { threadId: 5 } })).toBeNull();
    expect(agentRunThread({ threadId: "top-level" })).toBeNull();
  });
});

describe("shouldSendLimited", () => {
  it("sends once per window per key", () => {
    expect(shouldSendLimited("k1", 1_000_000)).toBe(true);
    expect(shouldSendLimited("k1", 1_000_000 + 59_999)).toBe(false);
    expect(shouldSendLimited("k1", 1_000_000 + 60_000)).toBe(true);
    expect(shouldSendLimited("k1", 1_000_000 + 60_001)).toBe(false);
  });

  it("tracks keys independently", () => {
    expect(shouldSendLimited("k2", 5_000_000)).toBe(true);
    expect(shouldSendLimited("k3", 5_000_001)).toBe(true);
    expect(shouldSendLimited("k2", 5_000_002)).toBe(false);
  });

  it("prunes keys past the window once there are more than 10,000, keeping those inside it", async () => {
    vi.resetModules(); // a fresh map, so keys from the other tests do not count
    const { limitedKeys, shouldSendLimited } = await import("../usage");
    const t = 9_000_000;
    for (let i = 0; i < 9_999; i++) shouldSendLimited(`stale-${i}`, t);
    shouldSendLimited("inside", t + 30_000); // 10,000 entries: no pruning yet
    expect(limitedKeys()).toContain("stale-0");
    shouldSendLimited("newest", t + 70_000); // 10,001 entries: prune; stale ones are 70s old, "inside" 40s
    expect(limitedKeys().sort()).toEqual(["inside", "newest"]);
  });
});

describe("recordQuestion", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    afterMock.mockReset();
    afterMock.mockImplementation((cb: () => unknown) => cb());
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("LANGGRAPH_DEPLOYMENT_URL", "http://agent:8123");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("does nothing without a usable token", () => {
    vi.stubEnv("ADMIN_TOKEN", "");
    recordQuestion({ ip: "1.2.3.4", threadId: "t", limited: false });
    vi.stubEnv("ADMIN_TOKEN", "s".repeat(31));
    recordQuestion({ ip: "1.2.3.4", threadId: "t", limited: false });
    expect(afterMock).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts inside after() with the agent contract", async () => {
    vi.stubEnv("ADMIN_TOKEN", TOKEN);
    let callback: (() => Promise<void>) | undefined;
    afterMock.mockImplementation((cb: () => Promise<void>) => {
      callback = cb;
    });
    recordQuestion({ ip: "1.2.3.4", threadId: "th-1", limited: true });
    expect(afterMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
    await callback!();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://agent:8123/admin/usage/question");
    expect(init.method).toBe("POST");
    expect(init.headers["x-admin-token"]).toBe(TOKEN);
    expect(init.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ ip: "1.2.3.4", thread_id: "th-1", limited: true });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("sends a null thread_id as null", async () => {
    vi.stubEnv("ADMIN_TOKEN", TOKEN);
    recordQuestion({ ip: "unknown", threadId: null, limited: false });
    await Promise.resolve();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ ip: "unknown", thread_id: null, limited: false });
  });

  it("swallows fetch rejection", async () => {
    vi.stubEnv("ADMIN_TOKEN", TOKEN);
    fetchMock.mockRejectedValue(new Error("down"));
    let result: Promise<unknown> | undefined;
    afterMock.mockImplementation((cb: () => Promise<unknown>) => {
      result = cb();
    });
    expect(() => recordQuestion({ ip: "1.2.3.4", threadId: null, limited: false })).not.toThrow();
    await expect(result).resolves.toBeUndefined();
  });

  it("swallows a synchronous fetch throw and an after() failure", async () => {
    vi.stubEnv("ADMIN_TOKEN", TOKEN);
    fetchMock.mockImplementation(() => {
      throw new Error("boom");
    });
    let result: Promise<unknown> | undefined;
    afterMock.mockImplementation((cb: () => Promise<unknown>) => {
      result = cb();
    });
    recordQuestion({ ip: "1.2.3.4", threadId: null, limited: false });
    await expect(result).resolves.toBeUndefined();
    afterMock.mockImplementation(() => {
      throw new Error("outside request scope");
    });
    expect(() => recordQuestion({ ip: "1.2.3.4", threadId: null, limited: false })).not.toThrow();
  });
});
