// 北极星的 service worker：只为了让手机能"安装"成 App。
// 不做离线缓存：对话、岗位、记忆都要实时从电脑上取，缓存旧页面反而会让人看到过期的进度。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {
  // 不拦截，浏览器照常联网
});
