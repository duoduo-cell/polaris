// iPhone"添加到主屏幕"的图标（iOS 自己会切圆角，所以这里画成铺满的方块）
import { ImageResponse } from "next/og";
import { mark } from "@/lib/icon";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(mark(180, { rounded: false, star: 0.5 }), size);
}
