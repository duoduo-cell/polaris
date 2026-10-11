"use client";

// 共用的小组件：markdown、卡片、徽标、按钮、骨架屏、空状态、页头。
import { motion } from "motion/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LucideIcon } from "lucide-react";

export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <div className={`prose max-w-none ${streaming ? "caret" : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

type Tone = "gray" | "blue" | "green" | "amber" | "red" | "violet";
const TONES: Record<Tone, string> = {
  gray: "bg-soft text-muted border-line",
  blue: "bg-accent-soft text-accent border-transparent",
  violet: "bg-violet-500/10 text-violet-600 dark:text-violet-300 border-transparent",
  green: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-transparent",
  amber: "bg-amber-500/12 text-amber-700 dark:text-amber-300 border-transparent",
  red: "bg-red-500/10 text-red-600 dark:text-red-300 border-transparent",
};

export function Badge({ children, tone = "gray", icon: Icon }: { children: React.ReactNode; tone?: Tone; icon?: LucideIcon }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${TONES[tone]}`}>
      {Icon && <Icon size={12} strokeWidth={2.2} />}
      {children}
    </span>
  );
}

export function Card({ children, className = "", hover = false, delay = 0 }: {
  children: React.ReactNode; className?: string; hover?: boolean; delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: "easeOut", delay }}
      whileHover={hover ? { y: -2 } : undefined}
      className={`rounded-2xl border border-line bg-card p-4 shadow-[0_1px_2px_rgba(16,24,40,0.04)] ${hover ? "hover:shadow-[0_8px_24px_rgba(16,24,40,0.08)] transition-shadow" : ""} ${className}`}
    >
      {children}
    </motion.div>
  );
}

export function Button({ children, onClick, disabled, variant = "primary", icon: Icon, className = "", type = "button" }: {
  children?: React.ReactNode; onClick?: () => void; disabled?: boolean; variant?: "primary" | "ghost" | "outline" | "danger";
  icon?: LucideIcon; className?: string; type?: "button" | "submit";
}) {
  const styles = {
    primary: "text-white bg-gradient-to-r from-accent to-accent-2 shadow-sm hover:brightness-110",
    ghost: "text-muted hover:text-foreground hover:bg-soft",
    outline: "border border-line bg-card hover:bg-soft",
    danger: "text-red-600 hover:bg-red-500/10",
  };
  return (
    <motion.button
      type={type}
      whileTap={disabled ? undefined : { scale: 0.96 }}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-sm font-medium transition disabled:opacity-40 disabled:cursor-not-allowed ${styles[variant]} ${className}`}
    >
      {Icon && <Icon size={15} />}
      {children}
    </motion.button>
  );
}

export function Skeleton({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={`skeleton ${className}`} style={style} />;
}

export function EmptyState({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children?: React.ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
                className="flex flex-col items-center text-center py-10 px-4">
      <div className="mb-3 grid place-items-center size-12 rounded-2xl bg-accent-soft text-accent">
        <Icon size={22} />
      </div>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-muted max-w-md">{children}</div>}
    </motion.div>
  );
}

export function PageHeader({ icon: Icon, title, children, right }: {
  icon: LucideIcon; title: string; children?: React.ReactNode; right?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 mb-5">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 grid place-items-center size-10 shrink-0 rounded-xl bg-gradient-to-br from-accent to-accent-2 text-white shadow-md">
          <Icon size={20} />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">{title}</h1>
          {children && <p className="text-sm text-muted mt-0.5">{children}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <motion.p initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
              className="text-sm text-red-600 bg-red-500/8 border border-red-500/20 rounded-xl px-3 py-2 my-2">
      {error}
    </motion.p>
  );
}
