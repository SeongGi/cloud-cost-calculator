import { beforeEach, describe, expect, it, vi } from "vitest";

const handleRequest = vi.hoisted(() => vi.fn());
const recordQuestion = vi.hoisted(() => vi.fn());

vi.mock("@copilotkit/runtime", () => ({
  CopilotRuntime: class {},
  ExperimentalEmptyAdapter: class {},
  copilotRuntimeNextJSAppRouterEndpoint: () => ({ handleRequest }),
}));
vi.mock("@/lib/copilotkit-runtime-options", () => ({ buildRuntimeOptions: () => ({}) }));
vi.mock("@/lib/usage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/usage")>()),
  recordQuestion,
}));

import type { NextRequest } from "next/server";

let POST: (req: NextRequest) => Promise<Response>;

const post = (body: unknown, ip: string) =>
  POST(
    new Request("http://localhost/api/copilotkit", {
      method: "POST",
      headers: { "x-real-ip": ip, "content-type": "application/json" },
      body: JSON.stringify(body),
    }) as NextRequest,
  );
const run = (threadId = "th-1") => ({ method: "agent/run", body: { threadId } });

describe("copilotkit route usage recording", () => {
  beforeEach(async () => {
    // Fresh route, rate-limit and usage modules per test, so their global Maps start empty.
    vi.resetModules();
    ({ POST } = await import("../route"));
    handleRequest.mockReset();
    handleRequest.mockResolvedValue(new Response("ok"));
    recordQuestion.mockReset();
  });

  it("records an agent run with its thread and limited=false", async () => {
    const res = await post(run("th-9"), "10.0.0.1");
    expect(res.status).toBe(200);
    expect(recordQuestion).toHaveBeenCalledTimes(1);
    expect(recordQuestion).toHaveBeenCalledWith({ ip: "10.0.0.1", threadId: "th-9", limited: false });
  });

  it("does not record connect or info requests", async () => {
    await post({ method: "agent/connect", body: { threadId: "th-1" } }, "10.0.0.2");
    await post({ method: "info" }, "10.0.0.2");
    await post("not an envelope", "10.0.0.2");
    expect(recordQuestion).not.toHaveBeenCalled();
    expect(handleRequest).toHaveBeenCalledTimes(3);
  });

  it("records limited=true once per window, after the rate-limit decision", async () => {
    const ip = "10.0.0.3";
    for (let i = 0; i < 10; i++) expect((await post(run(), ip)).status).toBe(200);
    expect(recordQuestion).toHaveBeenCalledTimes(10);
    expect(recordQuestion.mock.calls.every(([a]) => a.limited === false)).toBe(true);

    recordQuestion.mockClear();
    const first = await post(run("th-x"), ip);
    expect(first.status).toBe(429);
    expect(recordQuestion).toHaveBeenCalledTimes(1);
    expect(recordQuestion).toHaveBeenCalledWith({ ip, threadId: "th-x", limited: true });

    const second = await post(run(), ip);
    expect(second.status).toBe(429);
    expect(recordQuestion).toHaveBeenCalledTimes(1);
    expect(handleRequest).toHaveBeenCalledTimes(10);
  });

  it("passes unknown ip through", async () => {
    const req = new Request("http://localhost/api/copilotkit", {
      method: "POST",
      body: JSON.stringify({ method: "agent/run" }),
    });
    await POST(req as NextRequest);
    expect(recordQuestion).toHaveBeenCalledWith({ ip: "unknown", threadId: null, limited: false });
  });
});
