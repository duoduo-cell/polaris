"use client";

import { useEffect } from "react";

// 注册 service worker：安卓 Chrome 要它才肯"安装"成 App。只在安全的地址上注册（本机或 HTTPS，比如 Tailscale 给的地址）；
// 同一个 Wi-Fi 下用 http://192.168.x.x 打开时浏览器不允许，照样能用，只是"添加到主屏幕"变成普通书签。
export function RegisterServiceWorker() {
  useEffect(() => {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);
  return null;
}
