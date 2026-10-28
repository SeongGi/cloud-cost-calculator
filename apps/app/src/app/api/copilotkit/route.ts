import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { NextRequest } from "next/server";
import { buildRuntimeOptions } from "@/lib/copilotkit-runtime-options";
import { clientKey, isRateLimited, readRun } from "@/lib/rate-limit";
import { recordQuestion, shouldSendLimited } from "@/lib/usage";

export const POST = async (req: NextRequest) => {
  // Count only agent runs: one question also triggers connect/info requests.
  const { run, threadId } = await readRun(req);
  if (run) {
    const key = clientKey(req.headers);
    const limited = isRateLimited(key);
    // A limited visitor is reported once per rate-limit window; the rest of the flood is not recorded.
    if (!limited || shouldSendLimited(key)) recordQuestion({ ip: key, threadId, limited });
    if (limited) {
      return Response.json(
        { error: "요청이 많습니다. 잠시 후 다시 시도해 주세요." },
        { status: 429, headers: { "Retry-After": "60" } },
      );
    }
  }
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    endpoint: "/api/copilotkit",
    serviceAdapter: new ExperimentalEmptyAdapter(),
    runtime: new CopilotRuntime(buildRuntimeOptions({ langgraphUrl: process.env.LANGGRAPH_DEPLOYMENT_URL })),
  });
  return handleRequest(req);
};
