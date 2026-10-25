import { beforeEach, describe, expect, it } from "vitest";
import { clearChat, loadChat, saveChat } from "../chat-store";

describe("chat-store", () => {
  beforeEach(() => localStorage.clear());

  it("keeps the thread and the latest messages", () => {
    const messages = Array.from({ length: 100 }, (_, i) => ({ id: String(i), role: "user", content: `q${i}` }));
    saveChat("thread-1", messages);
    const saved = loadChat();
    expect(saved?.threadId).toBe("thread-1");
    expect(saved?.messages).toHaveLength(80);
    expect((saved?.messages.at(-1) as { id: string }).id).toBe("99");
    clearChat();
    expect(loadChat()).toBeNull();
  });

  it("ignores broken or stale data", () => {
    localStorage.setItem("cost-chat-v1", "{not json");
    expect(loadChat()).toBeNull();
    localStorage.setItem("cost-chat-v1", JSON.stringify({ threadId: "t", messages: [], savedAt: 0 }));
    expect(loadChat()).toBeNull();
  });
});
