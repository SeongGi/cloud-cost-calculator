import { beforeEach, describe, expect, it } from "vitest";
import { currentThread, deleteChat, listChats, loadMessages, sanitize, saveChat, setCurrent } from "../chat-store";

const user = (id: string, content: string) => ({ id, role: "user", content });

describe("chat-store", () => {
  beforeEach(() => localStorage.clear());

  it("keeps several conversations and reopens them", () => {
    saveChat("t1", [user("1", "Azure VM 3대 비용")]);
    saveChat("t2", [user("2", "AWS EC2 비용")]);
    expect(currentThread()).toBe("t2");
    expect(listChats().map((c) => c.title)).toEqual(["AWS EC2 비용", "Azure VM 3대 비용"]);
    setCurrent("t1");
    expect(currentThread()).toBe("t1");
    expect(loadMessages("t1")).toHaveLength(1);
    deleteChat("t1");
    expect(listChats().map((c) => c.threadId)).toEqual(["t2"]);
    expect(currentThread()).toBeNull();
  });

  it("drops tool calls that never got a result, and orphan results", () => {
    const messages = [
      user("u1", "여기에 vpn 추가"),
      { id: "a1", role: "assistant", content: "", toolCalls: [{ id: "c1" }, { id: "c2" }] },
      { id: "t1", role: "tool", toolCallId: "c1", content: "{}" },
      { id: "t9", role: "tool", toolCallId: "zz", content: "{}" },
      { id: "a2", role: "assistant", content: "", toolCalls: [{ id: "c3" }] },
    ];
    const clean = sanitize(messages) as { id: string; toolCalls?: { id: string }[] }[];
    expect(clean.map((m) => m.id)).toEqual(["u1", "a1", "t1"]);
    expect(clean[1].toolCalls?.map((c) => c.id)).toEqual(["c1"]);
  });

  it("ignores broken data and the old single-chat format", () => {
    localStorage.setItem("cost-chat-v1", JSON.stringify({ threadId: "old", messages: [], savedAt: Date.now() }));
    localStorage.setItem("cost-chats-v2", "{not json");
    expect(currentThread()).toBeNull();
    expect(localStorage.getItem("cost-chat-v1")).toBeNull();
    saveChat("t", [{ id: "x", role: "assistant", content: "hi" }]);
    expect(listChats()).toEqual([]); // nothing asked yet: not worth listing
  });
});
