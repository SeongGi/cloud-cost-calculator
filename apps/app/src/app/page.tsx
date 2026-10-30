"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { CopilotChat, useAgent, useConfigureSuggestions, useCopilotKit } from "@copilotkit/react-core/v2";
import { ReaderScrollView } from "@/components/chat/reader-scroll-view";
import { AnswerMarkdown } from "@/components/chat/answer-markdown";
import { useCostTool } from "@/hooks/use-cost-tool";
import { useTheme } from "@/hooks/use-theme";
import { describeError } from "@/lib/errors";
import { SITE } from "@/lib/site";
import { followUps, latestResult } from "@/lib/follow-ups";
import { currentThread, deleteChat, listChats, loadMessages, saveChat, setCurrent, type ChatSummary } from "@/lib/chat-store";

// The saved chat only changes through this page, so there is nothing to subscribe to.
const noSubscribe = () => () => {};

function newThreadId(): string {
  // crypto.randomUUID needs a secure context; plain-HTTP LAN access falls back to getRandomValues.
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// Example questions, four per cloud. Each was run through the local model and engine and priced as
// written. GCP has no Tokyo example: the model maps "GCP 도쿄" to Seoul.
const EXAMPLE_POOLS = [
  [
    { title: "AWS 웹 서비스", message: "AWS 서울에 t3.medium 2대, RDS PostgreSQL, S3 500GB, ALB 월 비용 알려줘" },
    { title: "AWS 쿠버네티스", message: "AWS 도쿄에 EKS 클러스터와 m6i.large 3대, ALB 월 비용은?" },
    { title: "AWS 서버리스", message: "AWS 서울에 Lambda, API Gateway, DynamoDB로 만든 서버리스 API 월 비용" },
    { title: "AWS 미국 서비스", message: "AWS 미국 동부에 c6i.large 4대, Aurora MySQL, ElastiCache 월 비용은?" },
  ],
  [
    { title: "Azure VM과 DB", message: "Azure 코리아 센트럴에 D2s v5 VM 3대랑 MySQL 월 비용은?" },
    { title: "Azure 쿠버네티스", message: "Azure 일본 동부(도쿄)에 AKS와 PostgreSQL, Blob Storage 1000GB 월 비용" },
    { title: "Azure 웹 서비스", message: "Azure 코리아 센트럴에 D4s v5 VM 2대, Application Gateway, Redis 월 비용은?" },
    { title: "Azure 서버리스", message: "Azure 미국 동부에 Azure SQL Database와 Functions 월 비용 알려줘" },
  ],
  [
    { title: "GCP 기본 구성", message: "GCP 서울에 e2-standard-2 VM 하나와 Cloud Storage 1000GB" },
    { title: "GCP VM과 캐시", message: "GCP 서울에 n2-standard-4 VM 3대와 Memorystore Redis 5GB 월 비용" },
    { title: "GCP VM과 DB", message: "GCP 미국 중부에 e2-standard-4 VM 2대와 Cloud SQL MySQL 월 비용은?" },
    { title: "GCP 데이터 분석", message: "GCP 미국에 BigQuery와 Cloud Storage 5000GB 월 비용 알려줘" },
  ],
];
// The server-rendered intro shows the first of each so the markup does not change between renders.
const EXAMPLES = EXAMPLE_POOLS.map((pool) => pool[0]);
const pickExamples = () => EXAMPLE_POOLS.map((pool) => pool[Math.floor(Math.random() * pool.length)]);

export default function HomePage() {
  // The thread id is the agent's memory key. It is decided after mount (the last chat in this
  // browser, or a new one) and handed to CopilotKit, which then loads that thread's history.
  // The server and the hydrating render read null and show the intro; then "" means no saved chat.
  const saved = useSyncExternalStore(noSubscribe, () => currentThread() ?? "", () => null);
  const [fresh] = useState(newThreadId);
  const [picked, setPicked] = useState<string | null>(null);
  const threadId = picked ?? (saved === null ? null : saved || fresh);
  const open = (id: string) => {
    setCurrent(id);
    setPicked(id);
  };
  if (!threadId) return <Intro />;
  return <Chat key={threadId} threadId={threadId} onOpen={open} onNewChat={() => open(newThreadId())} />;
}

// The chat needs the browser (its thread id lives there), so the server sends this introduction:
// the same header and the welcome text with example questions, which the chat then replaces.
function Intro() {
  return (
    <div className="chat-app">
      <header className="chat-header">
        <span className="chat-name">{SITE.name}</span>
      </header>
      <main id="main-content" className="chat-main chat-intro">
        <h1>AWS·Azure·GCP 월 비용을 말로 계산하세요</h1>
        <p>
          클라우드 구성을 한국어 문장으로 적으면 공개 가격표(온디맨드 정가)로 월 비용을 계산해 합계, 서비스별 비중, 항목별
          계산식을 보여 줍니다. 수량을 바꾸거나 &ldquo;여기에 VPN 추가&rdquo;처럼 이어서 물을 수 있습니다.
        </p>
        <ul>
          {EXAMPLES.map((e) => (
            <li key={e.title}>{e.message}</li>
          ))}
        </ul>
        <p>
          만든 사람 <a href={SITE.about}>{SITE.author}</a> · <a href={SITE.blog}>블로그</a> · <a href={SITE.repo}>GitHub</a>
        </p>
      </main>
    </div>
  );
}

function usePhone(): boolean {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 640px)");
    const update = () => setPhone(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return phone;
}

function relativeTime(ms: number): string {
  const min = Math.round((Date.now() - ms) / 60000);
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  if (min < 60 * 24) return `${Math.round(min / 60)}시간 전`;
  return new Date(ms).toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}

function ChatList({ current, onOpen, onClose }: { current: string; onOpen: (id: string) => void; onClose: () => void }) {
  const [chats, setChats] = useState<ChatSummary[]>(() => listChats());
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="chat-list-backdrop" onClick={onClose}>
      <section className="chat-list" role="dialog" aria-label="대화 목록" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>대화 목록</h2>
          <button type="button" onClick={onClose} aria-label="닫기">닫기</button>
        </header>
        {chats.length === 0 ? (
          <p className="chat-list-empty">아직 저장된 대화가 없습니다.</p>
        ) : (
          <ul>
            {chats.map((c) => (
              <li key={c.threadId} aria-current={c.threadId === current ? "true" : undefined}>
                <button type="button" className="chat-list-open" onClick={() => onOpen(c.threadId)}>
                  <span className="chat-list-title">{c.title}</span>
                  <span className="chat-list-time">{relativeTime(c.updatedAt)}</span>
                </button>
                <button
                  type="button"
                  className="chat-list-delete"
                  aria-label={`${c.title} 삭제`}
                  onClick={() => {
                    deleteChat(c.threadId);
                    setChats(listChats());
                  }}
                >
                  삭제
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="chat-list-note">대화는 이 브라우저에만 최대 30개, 30일 동안 저장됩니다.</p>
        <p className="chat-list-credit">
          만든 사람 <a href={SITE.about} target="_blank" rel="noopener">{SITE.author}</a> ·{" "}
          <a href={SITE.blog} target="_blank" rel="noopener">블로그</a> ·{" "}
          <a href={SITE.repo} target="_blank" rel="noopener noreferrer">GitHub</a>
        </p>
      </section>
    </div>
  );
}

function Chat({ threadId, onOpen, onNewChat }: { threadId: string; onOpen: (id: string) => void; onNewChat: () => void }) {
  useCostTool();
  const { agent } = useAgent({ threadId });
  // Examples on an empty chat; after an answer, questions people tend to ask next about it.
  // Chosen here because CopilotKit judges "first message" on its base agent, not this thread.
  // The chat renders only in the browser, so a fresh pick per chat is safe here.
  const [examples] = useState(pickExamples);
  const nextQuestions = useMemo(() => followUps(latestResult(agent.messages)), [agent.messages]);
  const suggestions = agent.messages.length === 0 ? examples : nextQuestions.map((q) => ({ title: q, message: q }));
  useConfigureSuggestions({ suggestions, available: "always" }, [suggestions.map((s) => s.message).join("|")]);
  const { copilotkit } = useCopilotKit();
  const { resolved, setTheme } = useTheme();
  const phone = usePhone();
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

  const [showList, setShowList] = useState(false);
  const [slow, setSlow] = useState(false);

  // CopilotKit first loads the thread's history from the app server. When that comes back empty
  // (e.g. after the server restarted), draw this browser's copy once it settles. Only while nothing
  // is shown, so nothing is doubled and a question typed meanwhile wins.
  useEffect(() => {
    const saved = loadMessages(threadId);
    if (!saved.length) return;
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      if (agent.messages.length > 0 || tries > 50) return clearInterval(timer);
      if (!agent.isRunning && tries >= 3) {
        agent.setMessages(saved as typeof agent.messages);
        clearInterval(timer);
      }
    }, 300);
    return () => clearInterval(timer);
  }, [agent, threadId]);

  // A run that never finishes must not lock the chat: offer to stop it after a while.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const subscription = agent.subscribe({
      onRunInitialized: () => {
        setSlow(false);
        clearTimeout(timer);
        timer = setTimeout(() => setSlow(true), 30000);
      },
      onRunFinalized: () => {
        clearTimeout(timer);
        setSlow(false);
      },
      onRunFailed: () => {
        clearTimeout(timer);
        setSlow(false);
      },
    });
    return () => {
      clearTimeout(timer);
      subscription.unsubscribe();
    };
  }, [agent]);

  const stopRun = () => {
    agent.abortRun();
    setSlow(false);
    setError("응답이 너무 오래 걸려 중지했습니다. 같은 질문을 다시 보내 주세요.");
  };

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

  const openChat = (id: string) => {
    setShowList(false);
    if (id !== threadId) onOpen(id);
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
        <span className="chat-name">{SITE.name}</span>
        <nav aria-label="주 메뉴">
          <button type="button" onClick={() => setShowList(true)} aria-haspopup="dialog" aria-label="대화 목록">
            <span className="chat-hide-sm">대화 </span>목록
          </button>
          <button type="button" onClick={newChat} disabled={agent.isRunning}>새 대화</button>
          <button type="button" onClick={() => setTheme(dark ? "light" : "dark")} aria-label={dark ? "라이트 모드로" : "다크 모드로"}>
            {dark ? "라이트" : "다크"}
          </button>
          <a className="chat-hide-md" href={SITE.blog} target="_blank" rel="noopener">블로그</a>
          <a className="chat-hide-md" href={SITE.about} target="_blank" rel="noopener">만든 사람</a>
          <a className="chat-hide-sm" href={SITE.repo} target="_blank" rel="noopener noreferrer">GitHub ↗</a>
        </nav>
      </header>
      <main id="main-content" className="chat-main">
        {showList && <ChatList current={threadId} onOpen={openChat} onClose={() => setShowList(false)} />}
        {slow && agent.isRunning && (
          <div className="chat-error" role="alert">
            <p>응답이 평소보다 오래 걸리고 있습니다.</p>
            <button type="button" onClick={stopRun}>중지하고 다시 보내기</button>
          </div>
        )}
        {error && (
          <div className="chat-error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={retry} disabled={agent.isRunning}>다시 시도</button>
          </div>
        )}
        <div className="chat-content" ref={chatRoot}>
          {phone && agent.messages.length === 0 && (
            <p className="chat-phone-hello">어떤 클라우드 구성의 월 비용을 계산할까요? 아래 예시를 누르거나 직접 적어 주세요.</p>
          )}
          <div className="chat-copilot">
            <CopilotChat
              // Phones: no centered welcome layout, so the input sits at the bottom from the start.
              welcomeScreen={phone ? false : undefined}
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
                chatInputPlaceholder: phone ? "구성을 적어 주세요" : "예: AWS EC2 2대, S3 500GB",
                chatDisclaimerText: "온디맨드 정가 기준 · 지난 대화는 '목록'에",
              }}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
