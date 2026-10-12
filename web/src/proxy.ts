// 手机模式下的门：没配对的设备打不开任何页面，接口一律 401。没开手机模式（只听本机）时直接放行。
import { NextResponse, type NextRequest } from "next/server";
import { PAIR_COOKIE, pairCode, pairToken, same } from "@/lib/pair";

export async function proxy(req: NextRequest) {
  const code = pairCode();
  if (!code) return NextResponse.next();
  const got = req.cookies.get(PAIR_COOKIE)?.value ?? "";
  if (got && same(got, await pairToken(code))) return NextResponse.next();
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "这台设备还没配对：在电脑上运行 start.ps1 -Phone，用手机扫窗口里的二维码" }, { status: 401 });
  }
  // 原地显示配对页（内部改写，不跳转）：经 Tailscale 等转发进来时请求里的主机名可能是 127.0.0.1，拼成跳转地址手机就打不开了
  return NextResponse.rewrite(new URL("/pair", req.url));
}

export const config = {
  // 配对页、图标、清单、service worker 不用配对（它们不含个人数据；iOS 取清单时不带 cookie）
  matcher: ["/((?!pair|_next/static|_next/image|favicon.ico|apple-icon|manifest.webmanifest|pwa-icon|sw.js).*)"],
};
