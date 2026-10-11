"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { BarChart3, BookOpen, Brain, Briefcase, MessageCircle, Network, Sparkles, Sun } from "lucide-react";

const NAV = [
  { href: "/", label: "今天", icon: Sun },
  { href: "/chat", label: "对话", icon: MessageCircle },
  { href: "/memory", label: "记忆", icon: Brain },
  { href: "/jobs", label: "岗位", icon: Briefcase },
  { href: "/interviews", label: "面经与内推", icon: BookOpen },
  { href: "/graph", label: "图谱", icon: Network },
  { href: "/evals", label: "评测", icon: BarChart3 },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav className="md:w-52 shrink-0 md:h-screen md:sticky md:top-0 border-b md:border-b-0 md:border-r border-line bg-card/70 backdrop-blur-xl px-3 py-3 md:py-6 flex md:flex-col gap-1 overflow-x-auto z-10">
      <Link href="/" className="flex items-center gap-2 px-2 mr-3 md:mr-0 md:mb-6 shrink-0">
        <motion.div
          className="grid place-items-center size-8 rounded-xl bg-gradient-to-br from-accent to-accent-2 text-white shadow-md"
          animate={{ rotate: [0, 8, -8, 0] }}
          transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
        >
          <Sparkles size={17} />
        </motion.div>
        <span className="font-bold text-lg tracking-tight hidden sm:inline">北极星</span>
      </Link>
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} className="relative rounded-xl px-3 py-2 text-sm whitespace-nowrap shrink-0">
            {active && (
              <motion.span layoutId="nav-active" className="absolute inset-0 rounded-xl bg-accent-soft"
                           transition={{ type: "spring", stiffness: 420, damping: 34 }} />
            )}
            <span className={`relative flex items-center gap-2.5 ${active ? "text-accent font-medium" : "text-muted hover:text-foreground"}`}>
              <Icon size={17} />
              {label}
            </span>
          </Link>
        );
      })}
      <p className="hidden md:block mt-auto px-3 text-xs text-muted leading-relaxed">
        记得你、懂行、主动但克制，<br />每件事都能核实。
      </p>
    </nav>
  );
}
