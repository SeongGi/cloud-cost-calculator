// User-facing wording for chat failures; the raw error is always logged to the console too.
export function describeError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error ?? "");
  if (/\b429\b|Too Many/i.test(text)) return "요청이 많습니다. 1분쯤 뒤에 다시 시도해 주세요.";
  if (/Failed to fetch|NetworkError|ECONNREFUSED|\b50[234]\b/i.test(text)) {
    return "계산 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  }
  return `답변 중 문제가 생겼습니다: ${text.slice(0, 160) || "알 수 없는 오류"}`;
}
