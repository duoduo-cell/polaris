import type { MetadataRoute } from "next";

// 手机"添加到主屏幕 / 安装"用的清单：全屏打开、从对话页进、图标和主题色跟网页一致
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "北极星 · 求职 agent",
    short_name: "北极星",
    description: "求职个人 agent：记得你、懂行、主动但克制、每件事都能核实",
    start_url: "/chat",
    scope: "/",
    display: "standalone",
    background_color: "#faf9f6",
    theme_color: "#faf9f6",
    lang: "zh-CN",
    icons: [
      { src: "/pwa-icon/192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/pwa-icon/512", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/pwa-icon/512?maskable=1", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
