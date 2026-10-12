// 主屏图标 PNG。?maskable=1 是安卓的"自适应图标"：铺满整块、星缩到中间安全区，系统会裁成圆形或圆角。
import { ImageResponse } from "next/og";
import { mark } from "@/lib/icon";

export async function GET(req: Request, ctx: RouteContext<"/pwa-icon/[size]">) {
  const { size } = await ctx.params;
  const px = size === "512" ? 512 : 192;
  const maskable = new URL(req.url).searchParams.has("maskable");
  return new ImageResponse(mark(px, { rounded: !maskable, star: maskable ? 0.42 : 0.58 }), {
    width: px, height: px, headers: { "Cache-Control": "public, max-age=86400" },
  });
}
