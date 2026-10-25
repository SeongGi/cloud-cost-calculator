import { LangGraphHttpAgent } from "@copilotkit/runtime/langgraph";

export interface RuntimeOptionsEnv {
  langgraphUrl?: string;
}

// Accept a bare host:port as well as a full URL.
export function normalizeLanggraphUrl(raw?: string): string {
  if (!raw) return "http://localhost:8123";
  return raw.startsWith("http") ? raw : `http://${raw}`;
}

// Only the cost agent: no A2UI, Open Generative UI or MCP tools are injected,
// so the local model sees exactly one tool (estimate_cost).
export function buildRuntimeOptions(env: RuntimeOptionsEnv) {
  return {
    agents: {
      default: new LangGraphHttpAgent({ url: normalizeLanggraphUrl(env.langgraphUrl) }),
    },
  };
}
