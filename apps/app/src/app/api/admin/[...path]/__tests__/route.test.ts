import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST } from "../route";

const GATE = "g".repeat(40);
const fetchMock = vi.fn();

const call = (method: "GET" | "POST", path: string, query = "") =>
  (method === "GET" ? GET : POST)(
    new NextRequest(`http://localhost/api/admin/${path}${query}`, {
      method,
      headers: { "x-admin-gate": GATE, "x-forwarded-email": "admin@example.com", "sec-fetch-site": "same-origin" },
    }),
    { params: Promise.resolve({ path: path.split("/") }) },
  );

describe("admin proxy usage route", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("ADMIN_GATE_SECRET", GATE);
    vi.stubEnv("ADMIN_EMAILS", "admin@example.com");
    vi.stubEnv("ADMIN_TOKEN", "t".repeat(32));
    vi.stubEnv("LANGGRAPH_DEPLOYMENT_URL", "http://agent:8123");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it.each(["7", "30", "90"])("forwards days=%s", async (days) => {
    const res = await call("GET", "usage", `?days=${days}`);
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(`http://agent:8123/admin/usage?days=${days}`);
    expect(fetchMock.mock.calls[0][1].headers["x-admin-token"]).toBe("t".repeat(32));
  });

  it("forwards without a query when days is absent, dropping other params", async () => {
    await call("GET", "usage");
    expect(fetchMock.mock.calls[0][0]).toBe("http://agent:8123/admin/usage");
    await call("GET", "usage", "?foo=bar");
    expect(fetchMock.mock.calls[1][0]).toBe("http://agent:8123/admin/usage");
    await call("GET", "usage", "?days=7&foo=bar");
    expect(fetchMock.mock.calls[2][0]).toBe("http://agent:8123/admin/usage?days=7");
  });

  it.each(["5", "", "07", "30.0", "abc", "-7", "7%20", "1e1"])("rejects days=%j with 400", async (days) => {
    const res = await call("GET", "usage", `?days=${days}`);
    expect(res.status).toBe(400);
    expect(await res.json()).toHaveProperty("error");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects repeated days", async () => {
    const res = await call("GET", "usage", "?days=7&days=30");
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not expose usage/question for GET or POST", async () => {
    expect((await call("GET", "usage/question")).status).toBe(404);
    expect((await call("POST", "usage/question")).status).toBe(404);
    expect((await call("POST", "usage")).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
