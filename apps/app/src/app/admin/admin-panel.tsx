"use client";

import { useCallback, useEffect, useState } from "react";

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
          {email} · <a href="/oauth2/sign_out?rd=%2F">로그아웃</a> · <a href="/">계산기로</a>
        </span>
      </header>

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
