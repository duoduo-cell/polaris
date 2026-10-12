// 配对：GET /pair?code=… 是扫码进来的；GET /pair 给一个手动输入的页面；POST /pair 是手动输入提交。
// 对了就发 cookie 并跳到对话页（地址栏里不留配对码）；错 8 次锁 10 分钟。
import { PAIR_COOKIE, PAIR_DAYS, pairCode, pairToken, same } from "@/lib/pair";

const failures: number[] = [];
const WINDOW_MS = 10 * 60 * 1000;
const MAX_FAILURES = 8;

function locked(): boolean {
  const now = Date.now();
  while (failures.length && now - failures[0] > WINDOW_MS) failures.shift();
  return failures.length >= MAX_FAILURES;
}

function page(message = "", status = 200): Response {
  const note = message ? `<p class="err">${message}</p>` : "";
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>配对 · 北极星</title>
<style>
:root{--bg:#faf9f6;--fg:#1f1e1c;--muted:#6b6862;--line:#e7e4dd;--card:#fff;--err:#b4232c}
@media (prefers-color-scheme:dark){:root{--bg:#1b1a18;--fg:#ecebe6;--muted:#a3a09a;--line:#34322e;--card:#23221f;--err:#f07178}}
body{margin:0;min-height:100dvh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;padding:24px;box-sizing:border-box}
main{width:100%;max-width:360px}h1{font-size:22px;margin:0 0 6px}p{color:var(--muted);margin:0 0 18px;font-size:14px}
input{width:100%;box-sizing:border-box;font-size:22px;letter-spacing:.18em;text-align:center;padding:12px;border-radius:12px;
border:1px solid var(--line);background:var(--card);color:var(--fg);text-transform:uppercase}
button{width:100%;margin-top:12px;padding:12px;border:0;border-radius:12px;background:var(--fg);color:var(--bg);font-size:16px}
.err{color:var(--err)}
</style></head><body><main>
<h1>配对这台设备</h1>
<p>在电脑上运行 <b>start.ps1 -Phone</b>，用手机扫窗口里的二维码；或者把窗口里的配对码输在下面。</p>${note}
<form method="post" action="/pair"><input name="code" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false"
placeholder="ABCD-EFGH" required><button type="submit">配对</button></form>
</main></body></html>`;
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

async function attempt(req: Request, raw: string | null): Promise<Response> {
  const code = pairCode();
  if (!code) return new Response(null, { status: 303, headers: { Location: "/chat" } });  // 没开手机模式，不用配对
  if (raw === null) return page();
  if (locked()) return page("试错次数太多，10 分钟后再试。", 429);
  const expected = await pairToken(code);
  if (!same(await pairToken(raw), expected)) {
    failures.push(Date.now());
    return page("配对码不对：看电脑上 start.ps1 窗口里的那串字。", 401);
  }
  const secure = new URL(req.url).protocol === "https:" || req.headers.get("x-forwarded-proto") === "https";
  const cookie = `${PAIR_COOKIE}=${expected}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${PAIR_DAYS * 86400}${secure ? "; Secure" : ""}`;
  return new Response(null, { status: 303, headers: { Location: "/chat", "Set-Cookie": cookie, "Cache-Control": "no-store" } });
}

export async function GET(req: Request) {
  return attempt(req, new URL(req.url).searchParams.get("code"));
}

export async function POST(req: Request) {
  const form = await req.formData().catch(() => null);
  return attempt(req, String(form?.get("code") ?? ""));
}
