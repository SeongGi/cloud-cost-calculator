import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { NextRequest } from "next/server";
import { buildRuntimeOptions } from "@/lib/copilotkit-runtime-options";
import { clientKey, isRateLimited, isAgentRun } from "@/lib/rate-limit";

export const POST = async (req: NextRequest) => {
  // Count only agent runs: one question also triggers connect/info requests.
  if (await isAgentRun(req) && isRateLimited(clientKey(req.headers))) {
    return Response.json(
      { error: "요청이 많습니다. 잠시 후 다시 시도해 주세요." },
      { status: 429, headers: { "Retry-After": "60" } },
    );
  }
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    endpoint: "/api/copilotkit",
    serviceAdapter: new ExperimentalEmptyAdapter(),
    runtime: new CopilotRuntime(buildRuntimeOptions({ langgraphUrl: process.env.LANGGRAPH_DEPLOYMENT_URL })),
  });
  return handleRequest(req);
};
