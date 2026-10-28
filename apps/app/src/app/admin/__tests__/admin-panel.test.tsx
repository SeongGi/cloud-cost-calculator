import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AdminPanel, UsageCard } from "../admin-panel";

const summary = (questions: number, over: Record<string, unknown> = {}) => ({
  days: 30,
  from: "2026-09-29",
  to: "2026-10-28",
  totals: {
    questions,
    limited: 0,
    visitors: 2,
    limited_visitors: 0,
    conversations: 3,
    estimates: 4,
    estimate_errors: 1,
    answer_failures: 2,
    busy: 3,
  },
  daily: [
    { day: "2026-10-27", questions: 1, visitors: 1 },
    { day: "2026-10-28", questions, visitors: 2 },
  ],
  clouds: { aws: 1, azure: 2, gcp: 3 },
  services: [{ service: "ec2", label: "EC2", count: 5 }],
  all_time: { questions: 99, visitors: 9, since: "2026-10-01" },
  ...over,
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("UsageCard", () => {
  it("shows the tiles and the all-time line", async () => {
    fetchMock.mockResolvedValue(json(summary(7)));
    render(<UsageCard />);
    await screen.findByText("기록 시작 2026-10-01부터 질문 99 · 방문자 9");
    expect(screen.getByText("계산 오류 1 · 답변 실패 2 · 혼잡 3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "30일" }).getAttribute("aria-pressed")).toBe("true");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/usage?days=30");
  });

  it("(a) does not break when all_time is null", async () => {
    fetchMock.mockResolvedValue(json(summary(7, { all_time: null })));
    render(<UsageCard />);
    expect(await screen.findByText("아직 기록이 없습니다")).toBeTruthy();
  });

  it("shows an error inside the card when the response has an unexpected shape", async () => {
    fetchMock.mockResolvedValue(json({ unexpected: true }));
    render(<UsageCard />);
    expect(await screen.findByText("사용량 응답 형식이 올바르지 않습니다.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "7일" })).toBeTruthy();
  });

  it("keeps a render failure inside the card", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    // Passes the shape check but breaks while rendering.
    fetchMock.mockResolvedValue(json(summary(7, { daily: [null] })));
    render(<UsageCard />);
    expect(await screen.findByText("사용량 응답을 표시하지 못했습니다.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "사용량" })).toBeTruthy();
  });

  it("(c) ignores a late response for a period that is no longer selected", async () => {
    const pending: { days: string; resolve: (r: Response) => void }[] = [];
    fetchMock.mockImplementation(
      (url: string) => new Promise<Response>((resolve) => pending.push({ days: url.split("days=")[1], resolve })),
    );
    render(<UsageCard />);
    fireEvent.click(screen.getByRole("button", { name: "7일" }));
    fireEvent.click(screen.getByRole("button", { name: "30일" }));
    await waitFor(() => expect(pending.map((p) => p.days)).toEqual(["30", "7", "30"]));
    const [first30, seven, second30] = pending;
    second30.resolve(json(summary(300)));
    await screen.findAllByText("300");
    // Late answers for the period switched away from, including the first 30-day request.
    seven.resolve(json(summary(7)));
    first30.resolve(json(summary(111)));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getAllByText("300").length).toBeGreaterThan(0);
    expect(screen.queryByText("111")).toBeNull();
    expect(screen.getByRole("button", { name: "30일" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("AdminPanel", () => {
  it("(b) keeps a usage request failure inside the usage card", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.startsWith("/api/admin/usage") ? json({ error: "usage broke" }, 502) : json({
        gemini_key_set: false,
        gemini_key_hint: "",
        gemini_model: "gemini-x",
        gemini_enabled: false,
        last_model: "",
        last_error: "",
        cooldown_seconds: 0,
        gemini_calls: 0,
        local_calls: 0,
        fallbacks: 0,
      }),
    );
    render(<AdminPanel email="admin@example.com" />);
    const usageCard = screen.getByRole("heading", { name: "사용량" }).closest("section")!;
    await within(usageCard).findByText("usage broke");
    const llmCard = (await screen.findByRole("heading", { name: "답변 모델" })).closest("section")!;
    await within(llmCard).findByText("로컬 모델만");
    expect(within(llmCard).queryByText("usage broke")).toBeNull();
    expect(screen.getAllByText("usage broke")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Gemini 설정" })).toBeTruthy();
  });
});
