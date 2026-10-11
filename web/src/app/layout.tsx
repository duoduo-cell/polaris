import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "北极星",
  description: "求职个人 agent：记得你、懂行、主动但克制、每件事都能核实",
};

const NAV = [
  { href: "/", label: "今天" },
  { href: "/chat", label: "对话" },
  { href: "/memory", label: "记忆" },
  { href: "/jobs", label: "岗位" },
  { href: "/graph", label: "图谱" },
  { href: "/evals", label: "评测" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col md:flex-row">
        <nav className="md:w-44 shrink-0 border-b md:border-b-0 md:border-r border-line bg-card px-4 py-3 md:py-6 flex md:flex-col gap-1 overflow-x-auto">
          <Link href="/" className="font-bold text-lg mr-4 md:mr-0 md:mb-4 whitespace-nowrap">
            北极星
          </Link>
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className="rounded-md px-3 py-1.5 text-sm whitespace-nowrap hover:bg-background"
            >
              {n.label}
            </Link>
          ))}
        </nav>
        <main className="flex-1 min-w-0 px-4 md:px-8 py-6 max-w-5xl">{children}</main>
      </body>
    </html>
  );
}
