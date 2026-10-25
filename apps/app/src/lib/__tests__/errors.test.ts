import { describe, expect, it } from "vitest";
import { describeError } from "../errors";

describe("describeError", () => {
  it("names the cause", () => {
    expect(describeError(new Error("HTTP 429: Too Many Requests"))).toContain("요청이 많습니다");
    expect(describeError(new TypeError("Failed to fetch"))).toContain("연결하지 못했습니다");
    expect(describeError(new Error("boom"))).toContain("boom");
  });
});
