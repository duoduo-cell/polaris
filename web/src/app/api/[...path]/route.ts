// 浏览器 → 这里 → 后端 /api/me/*：在服务端加上口令，浏览器拿不到口令。
import { API_BASE, ownerToken } from "@/lib/server";

type Ctx = RouteContext<"/api/[...path]">;

async function forward(req: Request, ctx: Ctx): Promise<Response> {
  const token = ownerToken();
  if (!token) {
    return Response.json({ detail: "没有配置 OWNER_TOKEN（仓库根目录的 .env）" }, { status: 503 });
  }
  const { path } = await ctx.params;
  const url = new URL(req.url);
  const target = `${API_BASE}/api/me/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "DELETE") init.body = await req.text();
  try {
    const res = await fetch(target, init);
    // 原样把响应体接过去：对话的流式事件（text/event-stream）要边收边转，不能等全部收完
    return new Response(res.body, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("Content-Type") ?? "application/json", "Cache-Control": "no-cache" },
    });
  } catch {
    return Response.json({ detail: "连不上后端：先运行 uv run polaris serve" }, { status: 502 });
  }
}

export const GET = forward;
export const POST = forward;
export const DELETE = forward;
