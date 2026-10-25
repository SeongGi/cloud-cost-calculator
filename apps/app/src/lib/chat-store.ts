// Conversations kept in this browser: each thread id (the agent's memory key) with its title and
// messages, so the list can reopen an earlier chat and a reload continues the current one.

const KEY = "cost-chats-v2"; // v1 (one chat, no cleanup of unfinished tool calls) is discarded
const OLD_KEYS = ["cost-chat-v1"];
const MAX_CHATS = 30;
const MAX_MESSAGES = 80;
const MAX_AGE_MS = 30 * 24 * 3600 * 1000;

export interface ChatSummary {
  threadId: string;
  title: string;
  updatedAt: number;
}

interface StoredChat extends ChatSummary {
  messages: unknown[];
}

interface Store {
  current: string | null;
  chats: Record<string, StoredChat>;
}

type Msg = { id?: unknown; role?: unknown; content?: unknown; toolCalls?: { id?: string }[]; toolCallId?: unknown };

function read(): Store {
  try {
    OLD_KEYS.forEach((k) => localStorage.removeItem(k));
    const raw = localStorage.getItem(KEY);
    const store = raw ? (JSON.parse(raw) as Store) : null;
    if (!store || typeof store.chats !== "object" || store.chats === null) return { current: null, chats: {} };
    const now = Date.now();
    for (const [id, chat] of Object.entries(store.chats)) {
      if (!chat || now - chat.updatedAt > MAX_AGE_MS || !Array.isArray(chat.messages)) delete store.chats[id];
    }
    return store;
  } catch {
    return { current: null, chats: {} };
  }
}

function write(store: Store): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    // Private mode or a full quota: the chat still works, it just is not kept.
  }
}

/** Keep only well-formed messages, and drop tool calls that never got a result (a run cut off by
 * a reload or an error): restoring them would leave the chat waiting for an answer forever. */
export function sanitize(messages: readonly unknown[]): unknown[] {
  const list = messages.filter(
    (m): m is Msg => !!m && typeof (m as Msg).id === "string" && typeof (m as Msg).role === "string",
  );
  const answered = new Set(list.filter((m) => m.role === "tool").map((m) => m.toolCallId));
  const out: unknown[] = [];
  for (const m of list) {
    if (m.role === "assistant" && Array.isArray(m.toolCalls) && m.toolCalls.length) {
      const calls = m.toolCalls.filter((c) => answered.has(c.id));
      if (!calls.length && !(typeof m.content === "string" && m.content.trim())) continue;
      out.push({ ...m, toolCalls: calls.length ? calls : undefined });
    } else {
      out.push(m);
    }
  }
  const calledIds = new Set(
    out.flatMap((m) => ((m as Msg).toolCalls ?? []).map((c) => c.id)),
  );
  return out.filter((m) => (m as Msg).role !== "tool" || calledIds.has((m as Msg).toolCallId as string));
}

function titleOf(messages: readonly unknown[]): string {
  const first = messages.find((m) => (m as Msg).role === "user") as Msg | undefined;
  const text = typeof first?.content === "string" ? first.content : "";
  return text.replace(/\s+/g, " ").trim().slice(0, 60) || "새 대화";
}

export function currentThread(): string | null {
  const store = read();
  return store.current && store.chats[store.current] ? store.current : null;
}

export function setCurrent(threadId: string): void {
  const store = read();
  store.current = threadId;
  write(store);
}

export function loadMessages(threadId: string): unknown[] {
  return sanitize(read().chats[threadId]?.messages ?? []);
}

export function saveChat(threadId: string, messages: readonly unknown[]): void {
  const clean = sanitize(messages);
  if (!clean.some((m) => (m as Msg).role === "user")) return;
  const store = read();
  // Strictly increasing, so the latest chat sorts first even within the same millisecond.
  const latest = Math.max(0, ...Object.values(store.chats).map((c) => c.updatedAt));
  const updatedAt = Math.max(Date.now(), latest + 1);
  store.chats[threadId] = { threadId, title: titleOf(clean), updatedAt, messages: clean.slice(-MAX_MESSAGES) };
  store.current = threadId;
  const ids = Object.values(store.chats).sort((a, b) => b.updatedAt - a.updatedAt).map((c) => c.threadId);
  ids.slice(MAX_CHATS).forEach((id) => delete store.chats[id]);
  write(store);
}

export function listChats(): ChatSummary[] {
  return Object.values(read().chats)
    .map(({ threadId, title, updatedAt }) => ({ threadId, title, updatedAt }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function deleteChat(threadId: string): void {
  const store = read();
  delete store.chats[threadId];
  if (store.current === threadId) store.current = null;
  write(store);
}
