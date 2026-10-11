// 浏览器里调后端：都走本站的 /api/*（Next.js 服务端代理加口令）。
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json" },
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: unknown }).detail;
    throw new ApiError(typeof detail === "string" ? detail : `出错了（${res.status}）`, res.status);
  }
  return data as T;
}

// 工具返回里包着的"外部原文"标记，显示时去掉
export function stripUntrusted(text: string | undefined | null): string {
  return (text ?? "")
    .replace(/<<<外部原文（[^）]*）\n?/g, "")
    .replace(/\n?外部原文>>>/g, "")
    .trim();
}

export async function tool<T = Record<string, unknown>>(name: string, args: Record<string, unknown>): Promise<T> {
  const r = await api<{ ok: boolean; result: unknown }>(`tools/${name}`, { body: { args } });
  if (!r.ok) throw new Error(typeof r.result === "string" ? r.result : "工具出错了");
  return r.result as T;
}

// 流式接口：服务端发来的 "data: {...}" 事件逐个交给 onEvent
export async function stream(path: string, body: unknown, onEvent: (e: Record<string, unknown>) => void): Promise<void> {
  const res = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { detail?: string }).detail ?? `出错了（${res.status}）`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6)));
    }
  }
}
