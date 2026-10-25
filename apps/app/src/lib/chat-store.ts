// Keeps the current conversation in this browser so a reload continues it: the thread id (the
// agent's memory key) and the messages (to draw the earlier answers and cost cards again).

const KEY = "cost-chat-v1";
const MAX_MESSAGES = 80;
const MAX_AGE_MS = 30 * 24 * 3600 * 1000;

export interface SavedChat {
  threadId: string;
  messages: readonly unknown[];
  savedAt: number;
}

export function loadChat(): SavedChat | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as SavedChat;
    if (typeof saved.threadId !== "string" || !Array.isArray(saved.messages)) return null;
    // Drop anything that is not a chat message (e.g. data from an older format).
    saved.messages = saved.messages.filter(
      (m) => !!m && typeof (m as { id?: unknown }).id === "string" && typeof (m as { role?: unknown }).role === "string",
    );
    if (Date.now() - saved.savedAt > MAX_AGE_MS) return null;
    return saved;
  } catch {
    return null;
  }
}

export function saveChat(threadId: string, messages: readonly unknown[]): void {
  try {
    const data: SavedChat = { threadId, messages: messages.slice(-MAX_MESSAGES), savedAt: Date.now() };
    localStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    // Private mode or a full quota: the chat still works, it just will not survive a reload.
  }
}

export function clearChat(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
