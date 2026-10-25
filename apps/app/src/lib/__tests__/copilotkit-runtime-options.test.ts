import { describe, expect, it } from "vitest";
import { buildRuntimeOptions, normalizeLanggraphUrl } from "../copilotkit-runtime-options";

describe("runtime options", () => {
  it("normalizes bare host:port", () => {
    expect(normalizeLanggraphUrl("agent:8123")).toBe("http://agent:8123");
    expect(normalizeLanggraphUrl(undefined)).toBe("http://localhost:8123");
  });

  it("injects no extra UI tools", () => {
    const options = buildRuntimeOptions({}) as Record<string, unknown>;
    expect(Object.keys(options)).toEqual(["agents"]);
  });
});
