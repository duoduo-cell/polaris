"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { Activity, BarChart3, BookOpen, Brain, Briefcase, Menu, MessageCircle, Network, Radar, Scale, Send, Sun, X } from "lucide-react";
import { useChatSession } from "@/components/chat-session";
import { api } from "@/lib/client";

const NAV = [
  { href: "/", label: "今天", icon: Sun },
  { href: "/chat", label: "对话", icon: MessageCircle },
  { href: "/memory", label: "记忆", icon: Brain },
  { href: "/jobs", label: "岗位", icon: Briefcase },
  { href: "/applications", label: "投递", icon: Send },
  { href: "/watches", label: "跟进", icon: Radar },
  { href: "/review", label: "竞争力", icon: Scale },
  { href: "/interviews", label: "面经与内推", icon: BookOpen },
  { href: "/graph", label: "图谱", icon: Network },
  { href: "/traces", label: "追踪", icon: Activity },
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
function AgentStatus({ className = "hidden md:block mt-auto mx-1" }: { className?: string }) {
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
    <Link href="/" className={`${className} rounded-xl border border-line bg-card px-3 py-2.5 text-xs hover:border-faint transition-colors`}>
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

function NavLinks({ path, layoutId, onPick }: { path: string; layoutId: string; onPick?: () => void }) {
  return NAV.map(({ href, label, icon: Icon }) => {
    const active = href === "/" ? path === "/" : path.startsWith(href);
    return (
      <Link key={href} href={href} onClick={onPick} aria-current={active ? "page" : undefined}
            className="relative rounded-lg px-2.5 py-1.5 max-md:py-2.5 text-sm max-md:text-[15px] whitespace-nowrap shrink-0">
        {active && (
          <motion.span layoutId={layoutId} className="absolute inset-0 rounded-lg bg-card border border-line"
                       transition={{ type: "spring", stiffness: 500, damping: 38 }} />
        )}
        <span className={`relative flex items-center gap-2.5 ${active ? "text-foreground font-medium" : "text-muted hover:text-foreground"}`}>
          <Icon size={16} aria-hidden />
          {label}
        </span>
      </Link>
    );
  });
}

// 手机：顶部一条（菜单、当前页、北极星忙不忙），点菜单从左边滑出全部页面和"北极星在做什么"。
// 对标 Claude、ChatGPT、Manus 的手机端：对话占满屏，其他页面收进抽屉；正在回答时在任何页面都看得到。
function MobileBar({ path }: { path: string }) {
  const drawer = useRef<HTMLDialogElement>(null);
  const { busy } = useChatSession();
  const current = NAV.find(({ href }) => (href === "/" ? path === "/" : path.startsWith(href)));
  const close = () => drawer.current?.close();
  useEffect(() => { drawer.current?.close(); }, [path]);  // 返回键、页面内跳转后不留着抽屉
  return (
    <header className="md:hidden sticky top-0 z-20 border-b border-line bg-background/90 backdrop-blur safe-top">
      <div className="h-13 px-2 flex items-center gap-1">
        <button type="button" aria-label="打开菜单" onClick={() => drawer.current?.showModal()}
                className="grid place-items-center size-10 rounded-lg text-muted active:bg-soft">
          <Menu size={20} aria-hidden />
        </button>
        <Link href="/" className="flex items-center gap-2 min-w-0">
          <PolarisMark size={24} />
          <span className="font-semibold tracking-tight truncate">{current?.label ?? "北极星"}</span>
        </Link>
        {busy && path !== "/chat" && (
          <Link href="/chat" className="ml-auto mr-1 flex items-center gap-1.5 rounded-full border border-line bg-card px-2.5 py-1 text-xs">
            <span className="live-dot" aria-hidden />正在回答
          </Link>
        )}
      </div>
      <dialog ref={drawer} className="drawer" aria-label="菜单"
              onClick={(e) => { if (e.target === drawer.current || (e.target as HTMLElement).closest("a")) close(); }}>
        <div className="drawer-body flex flex-col h-full px-3">
          <div className="flex items-center justify-between px-2 mb-4">
            <span className="flex items-center gap-2.5"><PolarisMark /><span className="font-semibold tracking-tight">北极星</span></span>
            <button type="button" aria-label="关闭菜单" onClick={close} className="grid place-items-center size-9 rounded-lg text-muted active:bg-soft">
              <X size={18} aria-hidden />
            </button>
          </div>
          <nav className="flex flex-col gap-0.5" aria-label="页面">
            <NavLinks path={path} layoutId="nav-active-mobile" onPick={close} />
          </nav>
          <AgentStatus className="block mt-auto" />
        </div>
      </dialog>
    </header>
  );
}

export function Nav() {
  const path = usePathname();
  return (
    <>
      <MobileBar path={path} />
      <nav className="hidden md:flex md:flex-col w-56 shrink-0 h-screen sticky top-0 border-r border-line bg-soft/50 px-3 py-5 gap-0.5 z-10" aria-label="页面">
        <Link href="/" className="flex items-center gap-2.5 px-2 mb-5 shrink-0">
          <PolarisMark />
          <span className="font-semibold tracking-tight">北极星</span>
        </Link>
        <NavLinks path={path} layoutId="nav-active" />
        <AgentStatus />
      </nav>
    </>
  );
}
