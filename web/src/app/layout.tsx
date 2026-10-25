import type { Metadata, Viewport } from "next";
import { ChatSessionProvider } from "@/components/chat-session";
import { DemoModeProvider } from "@/components/demo-mode";
import { Nav } from "@/components/nav";
import { RegisterServiceWorker } from "@/components/pwa";
import { demoMode } from "@/lib/server";
import "./globals.css";

export const metadata: Metadata = {
  title: "北极星",
  description: "求职个人 agent：记得你、懂行、主动但克制、每件事都能核实",
  // 手机"添加到主屏幕"后像 App 一样全屏打开（iOS 不读 manifest 的这部分，要单独写）
  appleWebApp: { capable: true, title: "北极星", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",  // 全屏打开时内容铺到刘海和底部横条下面，靠 safe-area 内边距让开
  interactiveWidget: "resizes-content",  // 键盘弹出时页面跟着变矮，输入框不被挡住
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf9f6" },
    { media: "(prefers-color-scheme: dark)", color: "#1b1a18" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col md:flex-row">
        <DemoModeProvider demo={demoMode()}>
        <ChatSessionProvider>
          <Nav />
          <main className="main-pad flex-1 min-w-0 px-4 md:px-10">
            <div className="mx-auto max-w-5xl">
              {demoMode() && <p role="status" className="my-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
                合成演示：预置岗位、面经、公司名称及个人档案都是虚构的，不代表实际招聘。请勿输入真实个人资料。
              </p>}
              {children}
            </div>
          </main>
        </ChatSessionProvider>
        </DemoModeProvider>
        <RegisterServiceWorker />
      </body>
    </html>
  );
}
