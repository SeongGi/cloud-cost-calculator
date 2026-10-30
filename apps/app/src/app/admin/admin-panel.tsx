"use client";

import { Component, useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";

interface LlmState {
  gemini_key_set: boolean;
  gemini_key_hint: string;
  gemini_model: string;
  gemini_enabled: boolean;
  last_model: string;
  last_error: string;
  cooldown_seconds: number;
  gemini_calls: number;
  local_calls: number;
  fallbacks: number;
}

interface UsageSummary {
  days: number;
  from: string;
  to: string;
  totals: {
    questions: number;
    limited: number;
    visitors: number;
    limited_visitors: number;
    conversations: number;
    estimates: number;
    estimate_errors: number;
    answer_failures: number;
    busy: number;
  };
  daily: { day: string; questions: number; visitors: number }[];
  clouds: { aws: number; azure: number; gcp: number };
  services: { service: string; label: string; count: number }[];
  // null is tolerated: an agent without records may send nothing here.
  all_time: { questions: number; visitors: number; since: string | null } | null;
}

const PERIODS = [7, 30, 90] as const;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/admin/${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // FastAPI validation errors carry a list of {msg} objects instead of a string.
    const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg?: string }) => d.msg).join(", ") : body.detail;
    throw new Error(detail ?? body.error ?? `요청 실패 (${res.status})`);
  }
  return body as T;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

// Only the shape the card reads; anything else is shown as an error inside the card.
function isUsageSummary(v: unknown): v is UsageSummary {
  return (
    isObject(v) &&
    isObject(v.totals) &&
    Array.isArray(v.daily) &&
    isObject(v.clouds) &&
    Array.isArray(v.services) &&
    (v.all_time === null || v.all_time === undefined || isObject(v.all_time))
  );
}

// A render error in the card body must not take down the LLM cards.
class UsageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? (
      <p className="admin-warn" role="status">사용량 응답을 표시하지 못했습니다.</p>
    ) : (
      this.props.children
    );
  }
}

// Loads on its own, so a usage failure shows only inside this card and the LLM cards keep working.
export function UsageCard() {
  const [days, setDays] = useState<number>(30);
  // The result remembers which period it answers, so switching periods shows "loading" without resetting state in the effect.
  const [result, setResult] = useState<{ days: number; usage?: UsageSummary; error?: string } | null>(null);

  useEffect(() => {
    let current = true;
    call<unknown>(`usage?days=${days}`)
      .then((body) => {
        if (!isUsageSummary(body)) throw new Error("사용량 응답 형식이 올바르지 않습니다.");
        if (current) setResult({ days, usage: body });
      })
      .catch((e: Error) => current && setResult({ days, error: e.message }));
    return () => {
      current = false;
    };
  }, [days]);

  const answered = result?.days === days ? result : null;
  const usage = answered?.usage ?? null;
  const error = answered?.error ?? "";

  return (
    <section className="admin-card" aria-labelledby="usage-title">
      <div className="admin-card-head">
        <h2 id="usage-title">사용량</h2>
        <div className="admin-periods" role="group" aria-label="기간">
          {PERIODS.map((n) => (
            <button key={n} type="button" aria-pressed={days === n} onClick={() => setDays(n)}>
              {n}일
            </button>
          ))}
        </div>
      </div>
      {error && <p className="admin-warn" role="status">{error}</p>}
      {!usage && !error && <p className="admin-note">불러오는 중…</p>}
      {usage && (
        <UsageBoundary key={days}>
          <UsageBody usage={usage} />
        </UsageBoundary>
      )}
      <p className="admin-note">질문 원문과 IP는 저장하지 않습니다. 방문자는 IP를 되돌릴 수 없는 값으로 바꿔 셉니다.</p>
    </section>
  );
}

function UsageBody({ usage }: { usage: UsageSummary }) {
  const t = usage.totals;
  const failures = t.estimate_errors + t.answer_failures + t.busy;
  const peak = Math.max(1, ...usage.daily.map((d) => d.questions));
  const allTime = usage.all_time;

  return (
    <>
      <p className="admin-sub">{usage.from} ~ {usage.to}</p>
      <dl className="admin-tiles">
        <div>
          <dt>질문</dt>
          <dd>{t.questions}</dd>
        </div>
        <div>
          <dt>방문자</dt>
          <dd>{t.visitors}</dd>
        </div>
        <div>
          <dt>대화</dt>
          <dd>{t.conversations}</dd>
        </div>
        <div>
          <dt>실패</dt>
          <dd>{failures}</dd>
          <dd className="admin-sub">계산 오류 {t.estimate_errors} · 답변 실패 {t.answer_failures} · 혼잡 {t.busy}</dd>
        </div>
      </dl>
      <p className="admin-sub">제한 {t.limited}회 · 제한된 방문자 {t.limited_visitors}</p>

      <table className="admin-daily">
        <caption className="admin-sr">일별 질문과 방문자</caption>
        <thead>
          <tr>
            <th scope="col">날짜</th>
            <th scope="col">질문</th>
            <th scope="col">방문자</th>
            <th scope="col"><span className="admin-sr">질문 수 막대</span></th>
          </tr>
        </thead>
        <tbody>
          {[...usage.daily].reverse().map((d) => (
            <tr key={d.day}>
              <th scope="row">{d.day}</th>
              <td>{d.questions}</td>
              <td>{d.visitors}</td>
              <td>
                <div className="admin-bar" title={`${d.day.slice(5)} 질문 ${d.questions} · 방문자 ${d.visitors}`}>
                  <div style={{ width: `${(d.questions / peak) * 100}%` }} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p>
        계산 클라우드: AWS {usage.clouds.aws} · Azure {usage.clouds.azure} · GCP {usage.clouds.gcp}
      </p>
      {usage.services.length > 0 && (
        <ol className="admin-services" aria-label="많이 계산한 서비스">
          {usage.services.map((s) => (
            <li key={s.service}>
              <span>{s.label}</span>
              <span>{s.count}</span>
            </li>
          ))}
        </ol>
      )}
      <p>
        {allTime?.since
          ? `기록 시작 ${allTime.since}부터 질문 ${allTime.questions} · 방문자 ${allTime.visitors}`
          : "아직 기록이 없습니다"}
      </p>
    </>
  );
}

export function AdminPanel({ email }: { email: string }) {
  const [state, setState] = useState<LlmState | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const s = await call<LlmState>("llm");
    setState(s);
    setModel((m) => m || s.gemini_model);
    if (s.gemini_key_set) call<{ models: string[] }>("llm/models").then((r) => setModels(r.models)).catch(() => setModels([]));
  }, []);

  useEffect(() => {
    refresh().catch((e) => setMessage({ ok: false, text: e.message }));
  }, [refresh]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setMessage({ ok: false, text: `${label}: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run("저장", async () => {
      const body: Record<string, unknown> = { gemini_model: model.trim() };
      if (key.trim()) body.gemini_api_key = key.trim();
      setState(await call<LlmState>("llm", { method: "PUT", body: JSON.stringify(body) }));
      setKey("");
      setMessage({ ok: true, text: "저장했습니다." });
      // The save already succeeded; a failing model list must not turn it into an error.
      refresh().catch(() => undefined);
    });

  const toggle = (enabled: boolean) =>
    run("전환", async () => {
      setState(await call<LlmState>("llm", { method: "PUT", body: JSON.stringify({ gemini_enabled: enabled }) }));
    });

  const test = () =>
    run("연결 시험", async () => {
      const r = await call<{ ok: boolean; quota?: boolean; error?: string; model?: string }>("llm/test", { method: "POST" });
      setMessage(
        r.ok
          ? { ok: true, text: `${r.model} 응답 정상` }
          : { ok: false, text: `${r.quota ? "한도 초과" : "실패"}: ${r.error}` },
      );
      await refresh();
    });

  const removeKey = () =>
    run("키 삭제", async () => {
      setState(await call<LlmState>("llm/key", { method: "DELETE" }));
      setModels([]);
      setMessage({ ok: true, text: "키를 지웠습니다. 이제 로컬 모델만 씁니다." });
    });

  const active = state ? state.gemini_key_set && state.gemini_enabled : false;

  return (
    <main className="admin">
      <header className="admin-head">
        <h1>관리자</h1>
        <span className="admin-user">
          {email} · <a href="/oauth2/sign_out?rd=%2F">로그아웃</a> · <Link href="/">계산기로</Link>
        </span>
      </header>

      <UsageCard />

      <section className="admin-card" aria-labelledby="llm-status">
        <h2 id="llm-status">답변 모델</h2>
        {state ? (
          <dl className="admin-grid">
            <dt>현재 방식</dt>
            <dd>
              {active ? `Gemini(${state.gemini_model}) 우선, 실패하면 로컬` : "로컬 모델만"}
              {active && state.cooldown_seconds > 0 && (
                <span className="admin-warn"> · Gemini 쉬는 중 {Math.ceil(state.cooldown_seconds / 60)}분</span>
              )}
            </dd>
            <dt>Gemini 키</dt>
            <dd>{state.gemini_key_set ? `저장됨 (${state.gemini_key_hint})` : "없음"}</dd>
            <dt>최근 답변 모델</dt>
            <dd>{state.last_model || "아직 없음"}</dd>
            <dt>호출 수</dt>
            <dd>
              Gemini {state.gemini_calls} · 로컬 {state.local_calls} · 전환 {state.fallbacks}
              <span className="admin-sub"> (에이전트 재시작 후)</span>
            </dd>
            {state.last_error && (
              <>
                <dt>최근 오류</dt>
                <dd className="admin-warn">{state.last_error}</dd>
              </>
            )}
          </dl>
        ) : (
          <p className="admin-note">불러오는 중…</p>
        )}
        {state?.gemini_key_set && (
          <label className="admin-switch">
            <input type="checkbox" checked={state.gemini_enabled} disabled={busy} onChange={(e) => toggle(e.target.checked)} />
            <span>Gemini 사용</span>
          </label>
        )}
      </section>

      <section className="admin-card" aria-labelledby="llm-form">
        <h2 id="llm-form">Gemini 설정</h2>
        <label className="admin-field">
          <span>API 키 {state?.gemini_key_set && <small>(바꿀 때만 입력)</small>}</span>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={state?.gemini_key_set ? `저장됨 ${state.gemini_key_hint}` : "Google AI Studio에서 만든 키"}
          />
        </label>
        <label className="admin-field">
          <span>모델</span>
          <input list="gemini-models" value={model} onChange={(e) => setModel(e.target.value)} spellCheck={false} />
          <datalist id="gemini-models">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>
        <div className="admin-actions">
          <button type="button" onClick={save} disabled={busy}>저장</button>
          <button type="button" onClick={test} disabled={busy || !state?.gemini_key_set}>연결 시험</button>
          <button type="button" className="danger" onClick={removeKey} disabled={busy || !state?.gemini_key_set}>키 삭제</button>
        </div>
        {message && (
          <p className={message.ok ? "admin-ok" : "admin-warn"} role="status">
            {message.text}
          </p>
        )}
        <p className="admin-note">
          키는 서버의 비공개 파일에만 저장되고 이 화면에는 끝 4자리만 보입니다. Gemini를 쓰면 방문자의 질문이 Google로 전송됩니다.
          한도 초과면 Google이 알려 준 대기 시간(최소 10분, 최대 6시간), 그 밖의 오류면 2분 동안 로컬 모델로 답합니다. 무료 등급은 모델마다 하루 한도가 다릅니다(Flash 20회 등).
        </p>
      </section>
    </main>
  );
}
