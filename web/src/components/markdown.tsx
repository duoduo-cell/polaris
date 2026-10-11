"use client";

// 共用的小组件：markdown、卡片、徽标、按钮、骨架屏、空状态、页头、匹配环。风格素净，动画只用在状态变化上。
import { useMemo } from "react";
import { motion } from "motion/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LucideIcon } from "lucide-react";
import { refsPlugin } from "@/lib/refs";

// refs：回答里能点开的编号 → 名字；传了就把其余编号去掉（不传时原样显示，评测报告用）
export function Markdown({ text, streaming = false, refs, onRef }: {
  text: string; streaming?: boolean; refs?: Map<string, string>; onRef?: (id: string) => void;
}) {
  const plugins = useMemo(() => (refs ? [remarkGfm, refsPlugin(refs)] : [remarkGfm]), [refs]);
  return (
    <div className={`prose max-w-none ${streaming ? "caret" : ""}`}>
      <ReactMarkdown remarkPlugins={plugins} components={{
        a: ({ href, children }) => href?.startsWith("#entity-")
          ? <button type="button" className="ref" onClick={() => onRef?.(href.slice(8))}>{children}</button>
          : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      }}>{text}</ReactMarkdown>
    </div>
  );
}

type Tone = "gray" | "blue" | "green" | "amber" | "red";
const TONES: Record<Tone, string> = {
  gray: "bg-soft text-muted",
  blue: "bg-accent-soft text-accent",
  green: "bg-ok-soft text-ok",
  amber: "bg-warn-soft text-warn",
  red: "bg-bad-soft text-bad",
};

export function Badge({ children, tone = "gray", icon: Icon, className = "" }: {
  children: React.ReactNode; tone?: Tone | "violet"; icon?: LucideIcon; className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-md whitespace-nowrap ${TONES[tone === "violet" ? "blue" : tone]} ${className}`}>
      {Icon && <Icon size={12} strokeWidth={2.2} aria-hidden />}
      {children}
    </span>
  );
}

export function Card({ children, className = "", hover = false, delay = 0 }: {
  children: React.ReactNode; className?: string; hover?: boolean; delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut", delay }}
      className={`rounded-xl border border-line bg-card p-4 ${hover ? "transition-colors hover:border-faint" : ""} ${className}`}
    >
      {children}
    </motion.div>
  );
}

export function Button({ children, onClick, disabled, variant = "secondary", icon: Icon, className = "", type = "button", title, label }: {
  children?: React.ReactNode; onClick?: () => void; disabled?: boolean;
  variant?: "primary" | "secondary" | "ghost" | "outline" | "danger"; icon?: LucideIcon; className?: string;
  type?: "button" | "submit"; title?: string; label?: string;
}) {
  const styles = {
    primary: "bg-foreground text-background hover:opacity-90",
    secondary: "border border-line bg-card hover:bg-soft",
    outline: "border border-line bg-card hover:bg-soft",
    ghost: "text-muted hover:text-foreground hover:bg-soft",
    danger: "text-bad hover:bg-bad-soft",
  };
  return (
    <motion.button
      type={type}
      title={title}
      aria-label={label}
      whileTap={disabled ? undefined : { scale: 0.97 }}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${styles[variant]} ${className}`}
    >
      {Icon && <Icon size={15} aria-hidden />}
      {children}
    </motion.button>
  );
}

export function Skeleton({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={`skeleton ${className}`} style={style} />;
}

export function EmptyState({ icon: Icon, title, children, action }: {
  icon: LucideIcon; title: string; children?: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center text-center py-10 px-4">
      <div className="mb-3 grid place-items-center size-10 rounded-xl bg-soft text-muted"><Icon size={19} aria-hidden /></div>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-muted max-w-md leading-relaxed">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </motion.div>
  );
}

export function PageHeader({ icon: Icon, title, children, right }: {
  icon?: LucideIcon; title: string; children?: React.ReactNode; right?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-[1.35rem] font-semibold tracking-tight flex items-center gap-2">
          {Icon && <Icon size={20} className="text-muted" aria-hidden />}{title}
        </h1>
        {children && <p className="text-sm text-muted mt-1 leading-relaxed">{children}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <motion.p initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
              className="text-sm text-bad bg-bad-soft rounded-lg px-3 py-2 my-2">
      {error}
    </motion.p>
  );
}

// 匹配分：圆环从 0 画到分数，颜色按"很合适 / 基本合适 / 差得多"
export function ScoreRing({ score, size = 40, label = true }: { score: number; size?: number; label?: boolean }) {
  const r = (size - 5) / 2;
  const c = 2 * Math.PI * r;
  const color = score >= 80 ? "var(--ok)" : score >= 60 ? "var(--warn)" : "var(--bad)";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} title={`和你的条件匹配 ${score} 分`}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth="3.5" />
        <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="3.5" strokeLinecap="round"
                       strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - score / 100) }}
                       transition={{ duration: 0.8, ease: "easeOut" }} />
      </svg>
      {label && <span className="absolute inset-0 grid place-items-center text-[0.8rem] font-semibold tabular-nums" style={{ color }}>{score}</span>}
    </div>
  );
}
