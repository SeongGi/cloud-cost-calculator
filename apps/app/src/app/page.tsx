"use client";

import { useEffect, useRef, useState } from "react";
import { CopilotChat, useAgent, useConfigureSuggestions, useCopilotKit } from "@copilotkit/react-core/v2";
import { ReaderScrollView } from "@/components/chat/reader-scroll-view";
import { AnswerMarkdown } from "@/components/chat/answer-markdown";
import { useCostTool } from "@/hooks/use-cost-tool";
import { useTheme } from "@/hooks/use-theme";
import { describeError } from "@/lib/errors";
import { clearChat, loadChat, saveChat } from "@/lib/chat-store";

function newThreadId(): string {
  // crypto.randomUUID needs a secure context; plain-HTTP LAN access falls back to getRandomValues.
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const EXAMPLES = [
  { title: "AWS 웹 서비스", message: "AWS 서울에 t3.medium 2대, RDS PostgreSQL, S3 500GB, ALB 월 비용 알려줘" },
  { title: "Azure VM과 DB", message: "Azure 코리아 센트럴에 D2s v5 VM 3대랑 MySQL 월 비용은?" },
  { title: "GCP 기본 구성", message: "GCP 서울에 e2-standard-2 VM 하나와 Cloud Storage 1000GB" },
];

export default function HomePage() {
  // The thread id is the agent's memory key. It is decided after mount (from this browser's saved
  // chat or new) and handed to CopilotKit, which then loads that thread's history itself.
  const [threadId, setThreadId] = useState<string | null>(null);
  useEffect(() => {
    setThreadId(loadChat()?.threadId ?? newThreadId());
  }, []);
  return threadId ? <Chat key={threadId} threadId={threadId} onNewChat={() => { clearChat(); setThreadId(newThreadId()); }} /> : null;
}

function Chat({ threadId, onNewChat }: { threadId: string; onNewChat: () => void }) {
  useCostTool();
  useConfigureSuggestions({ suggestions: EXAMPLES, available: "before-first-message" });
  const { agent } = useAgent({ threadId });
  const { copilotkit } = useCopilotKit();
  const { resolved, setTheme } = useTheme();
  const [error, setError] = useState<string | null>(null);
  const chatRoot = useRef<HTMLDivElement>(null);
  const latestQuestion = agent.messages.filter((message) => message.role === "user").at(-1)?.id;

  // Move only when a question is submitted; streamed answers never take scroll ownership.
  useEffect(() => {
    if (!latestQuestion || !chatRoot.current) return;
    const root = chatRoot.current;
    const revealQuestion = () => {
      const question = Array.from(root.querySelectorAll<HTMLElement>('[data-testid="copilot-user-message"]'))
        .find((element) => element.dataset.messageId === latestQuestion);
      if (!question) return false;
      question.scrollIntoView?.({ block: "start", behavior: "instant" });
      return true;
    };
    if (revealQuestion()) return;
    const observer = new MutationObserver(() => {
      if (revealQuestion()) observer.disconnect();
    });
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [latestQuestion]);

  // If CopilotKit's own history load came back empty (e.g. the app server restarted), draw the
  // earlier answers from this browser's copy. Only when nothing is shown, so nothing is doubled.
  useEffect(() => {
    const timer = setTimeout(() => {
      const saved = loadChat();
      if (saved?.threadId === threadId && !agent.isRunning && agent.messages.length === 0 && saved.messages.length) {
        agent.setMessages(saved.messages as typeof agent.messages);
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [agent, threadId]);

  useEffect(() => {
    const save = () => {
      if (agent.messages.length) saveChat(threadId, agent.messages);
    };
    const subscription = agent.subscribe({
      onRunInitialized: () => setError(null),
      onRunFinishedEvent: () => setError(null),
      // Save once per answer, not on every streamed token.
      onRunFinalized: save,
      onRunFailed: save,
    });
    return () => subscription.unsubscribe();
  }, [agent, threadId]);

  const newChat = () => {
    setError(null);
    onNewChat();
  };

  const retry = async () => {
    if (agent.isRunning) return;
    setError(null);
    try {
      await copilotkit.runAgent({ agent });
    } catch (err) {
      console.error("[chat] retry failed", err);
      setError(describeError(err));
    }
  };

  const dark = resolved === "dark";

  return (
    <div className="chat-app">
      <a className="chat-skip" href="#main-content">채팅으로 건너뛰기</a>
      <header className="chat-header">
        <span className="chat-name">클라우드 비용 계산기</span>
        <nav aria-label="주 메뉴">
          <button type="button" onClick={newChat} disabled={agent.isRunning}>새 대화</button>
          <button type="button" onClick={() => setTheme(dark ? "light" : "dark")} aria-label={dark ? "라이트 모드로" : "다크 모드로"}>
            {dark ? "라이트" : "다크"}
          </button>
          <a href="https://github.com/SeongGi/cloud-cost-calculator" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
        </nav>
      </header>
      <main id="main-content" className="chat-main">
        {error && (
          <div className="chat-error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={retry} disabled={agent.isRunning}>다시 시도</button>
          </div>
        )}
        <div className="chat-content" ref={chatRoot}>
          <CopilotChat
            threadId={threadId}
            autoScroll={false}
            scrollView={ReaderScrollView}
            messageView={{ assistantMessage: { markdownRenderer: AnswerMarkdown } }}
            onError={(event) => {
              const err = "error" in event ? event.error : event;
              console.error("[chat] agent error", err);
              setError(describeError(err));
            }}
            labels={{
              welcomeMessageText: "어떤 클라우드 구성의 월 비용을 계산할까요?",
              chatInputPlaceholder: "예: AWS t3.medium 2대, S3 500GB",
              chatDisclaimerText: "공개 가격표의 온디맨드 정가 기준입니다. 대화는 이 브라우저에 30일 보관되며 '새 대화'를 누르면 지워집니다.",
            }}
          />
        </div>
      </main>
    </div>
  );
}
