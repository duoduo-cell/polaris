"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { BarChart3, BookOpen, Brain, Briefcase, MessageCircle, Network, Sun } from "lucide-react";
import { api } from "@/lib/client";

const NAV = [
  { href: "/", label: "今天", icon: Sun },
  { href: "/chat", label: "对话", icon: MessageCircle },
  { href: "/memory", label: "记忆", icon: Brain },
  { href: "/jobs", label: "岗位", icon: Briefcase },
  { href: "/interviews", label: "面经与内推", icon: BookOpen },
  { href: "/graph", label: "图谱", icon: Network },
  { href: "/evals", label: "评测", icon: BarChart3 },
];

export type AgendaItem = { kind: string; text: string; when?: string; detail?: string; href?: string; job?: string };
export type Agenda = { doing?: AgendaItem[]; needs_you?: AgendaItem[]; scheduled?: AgendaItem[]; done?: AgendaItem[]; last_collect?: string | null };

export function PolarisMark({ size = 28 }: { size?: number }) {
  return (
    <span className="grid place-items-center rounded-lg bg-foreground text-background shrink-0" style={{ width: size, height: size }} aria-hidden>
      <svg width={size * 0.58} height={size * 0.58} viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 1.5l2.1 7.4L21.5 12l-7.4 2.1L12 22.5l-2.1-8.4L2.5 12l7.4-3.1z" />
      </svg>
    </span>
  );
}

// 侧栏底部：北极星此刻在做什么（Dots 式的常驻感），一分钟刷新一次
function AgentStatus() {
  const [agenda, setAgenda] = useState<Agenda | null>(null);
  useEffect(() => {
    let active = true;
    const load = () => api<Agenda>("agenda").then((a) => { if (active) setAgenda(a); }).catch(() => {});
    void load();
    const timer = setInterval(load, 60000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  if (!agenda?.doing && !agenda?.needs_you) return null;
  const watching = agenda.doing?.find((d) => d.kind === "watch");
  const chatting = agenda.doing?.find((d) => d.kind === "chat");
  const needs = (agenda.needs_you ?? []).filter((n) => n.kind !== "profile").length;
  const last = agenda.last_collect ? new Date(agenda.last_collect) : null;
  return (
    <Link href="/" className="hidden md:block mt-auto mx-1 rounded-xl border border-line bg-card px-3 py-2.5 text-xs hover:border-faint transition-colors">
      <span className="flex items-center gap-2 font-medium">
        <span className={chatting || watching ? "live-dot" : "size-[7px] rounded-full bg-faint"} aria-hidden />
        {chatting ? "正在回答你" : watching ? "在盯新岗位" : "还不知道你想找什么"}
      </span>
      {watching && <span className="block mt-1 text-muted leading-relaxed">{watching.text.replace(/^盯着/, "").replace(/的新岗位和帖子$/, "")}</span>}
      {needs > 0 && <span className="block mt-1 text-warn">{needs} 件事等你决定</span>}
      {last && <span className="block mt-1 text-faint" suppressHydrationWarning>
        上次采集 {last.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>}
    </Link>
  );
}

export function Nav() {
  const path = usePathname();
  return (
    <nav className="md:w-56 shrink-0 md:h-screen md:sticky md:top-0 border-b md:border-b-0 md:border-r border-line bg-soft/50 px-3 py-2.5 md:py-5 flex md:flex-col gap-0.5 overflow-x-auto z-10">
      <Link href="/" className="flex items-center gap-2.5 px-2 mr-2 md:mr-0 md:mb-5 shrink-0">
        <PolarisMark />
        <span className="font-semibold tracking-tight hidden sm:inline">北极星</span>
      </Link>
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} className="relative rounded-lg px-2.5 py-1.5 text-sm whitespace-nowrap shrink-0">
            {active && (
              <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg bg-card border border-line"
                           transition={{ type: "spring", stiffness: 500, damping: 38 }} />
            )}
            <span className={`relative flex items-center gap-2.5 ${active ? "text-foreground font-medium" : "text-muted hover:text-foreground"}`}>
              <Icon size={16} aria-hidden />
              {label}
            </span>
          </Link>
        );
      })}
      <AgentStatus />
    </nav>
  );
}
