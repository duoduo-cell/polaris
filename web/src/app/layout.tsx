import type { Metadata } from "next";
import { ChatSessionProvider } from "@/components/chat-session";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "北极星",
  description: "求职个人 agent：记得你、懂行、主动但克制、每件事都能核实",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col md:flex-row">
        <ChatSessionProvider>
          <Nav />
          <main className="flex-1 min-w-0 px-4 md:px-10 py-6 md:py-8">
            <div className="mx-auto max-w-5xl">{children}</div>
          </main>
        </ChatSessionProvider>
      </body>
    </html>
  );
}
