import { NextRequest } from "next/server";
import { adminEmail } from "@/lib/admin-auth";

// Only these agent admin calls are reachable, each with its method.
const ROUTES: Record<string, string[]> = {
  llm: ["GET", "PUT"],
  "llm/key": ["DELETE"],
  "llm/models": ["GET"],
  "llm/test": ["POST"],
  usage: ["GET"],
};

async function forward(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  if (!adminEmail(req.headers)) return Response.json({ error: "관리자만 쓸 수 있습니다." }, { status: 403 });
  // Changes must come from the admin page itself (blocks requests fired from sibling subdomains).
  if (req.method !== "GET" && req.headers.get("sec-fetch-site") !== "same-origin") {
    return Response.json({ error: "같은 사이트에서만 바꿀 수 있습니다." }, { status: 403 });
  }
  const path = (await params).path.join("/");
  if (!ROUTES[path]?.includes(req.method)) return Response.json({ error: "없는 경로입니다." }, { status: 404 });
  // Usage takes one query value, from a fixed list; any other query is dropped.
  let query = "";
  if (path === "usage") {
    const days = req.nextUrl.searchParams.getAll("days");
    if (days.length > 1 || (days.length === 1 && !["7", "30", "90"].includes(days[0]))) {
      return Response.json({ error: "days는 7, 30, 90 중 하나여야 합니다." }, { status: 400 });
    }
    if (days.length === 1) query = `?days=${days[0]}`;
  }
  const token = process.env.ADMIN_TOKEN ?? "";
  const base = process.env.LANGGRAPH_DEPLOYMENT_URL ?? "http://localhost:8123";
  const res = await fetch(`${base}/admin/${path}${query}`, {
    method: req.method,
    headers: { "content-type": "application/json", "x-admin-token": token },
    body: req.method === "PUT" ? await req.text() : undefined,
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

export { forward as GET, forward as PUT, forward as DELETE, forward as POST };
