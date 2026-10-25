// 浏览器 → 这里 → 后端 /api/me/*：在服务端加上口令，浏览器拿不到口令。
import { API_BASE, demoMode, ownerToken } from "@/lib/server";

type Ctx = RouteContext<"/api/[...path]">;

async function forward(req: Request, ctx: Ctx): Promise<Response> {
  const token = ownerToken();
  if (!token) {
    return Response.json({ detail: "没有配置 OWNER_TOKEN（当前选定配置）" }, { status: 503 });
  }
  const demo = demoMode();
  if (demo) {
    try {
      // 先查公开健康信息；不带口令、Cookie或业务正文，不信任上一次成功的结果。
      const health = await fetch(`${API_BASE}/health`, {
        method: "GET", cache: "no-store", credentials: "omit", redirect: "error", signal: AbortSignal.timeout(3000),
      });
      if (!health.ok) throw new Error();
      const data = await health.json();
      if (data?.status !== "ok" || data?.database !== true || data?.config?.dataset !== "synthetic_demo") throw new Error();
    } catch {
      return Response.json({ detail: "无法确认演示后端可用，已停止转发。请核对演示配置与服务状态。" }, { status: 503 });
    }
  }
  const { path } = await ctx.params;
  const url = new URL(req.url);
  const forwardedFor = req.headers.get("x-forwarded-for");
  const target = `${API_BASE}/api/me/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json",
      "X-Polaris-Expected-Dataset": demo ? "synthetic_demo" : "personal",
      // The demo API limits requests per visitor; Caddy puts the visitor's address here.
      ...(demo && forwardedFor ? { "X-Forwarded-For": forwardedFor } : {}) },
    cache: "no-store",
    ...(demo ? { redirect: "error" as const } : {}),
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
    return Response.json({ detail: demo ? "演示请求未完成，请核对演示后端状态后再试。"
      : "连不上后端：先运行 uv run polaris serve" }, { status: 502 });
  }
}

export const GET = forward;
export const POST = forward;
export const DELETE = forward;
