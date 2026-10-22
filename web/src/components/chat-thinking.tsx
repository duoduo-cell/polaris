"use client";

// 对话里的过程展示：思考过程（查了什么、查到多少）、记忆提示（记住了什么、可以删）、上下文压缩分隔线、上下文用量。
import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Archive, Brain, Check, ChevronRight, CircleAlert, CircleHelp, Globe, Loader2, Square, Undo2 } from "lucide-react";
import { Markdown } from "@/components/markdown";
import { type Compacted, type Live, type MemoryEvent, type Step, type Turn, TOOL_NAMES } from "@/components/chat-session";
import { TraceTimeline } from "@/components/trace";
import { api } from "@/lib/client";
import { hideRefs } from "@/lib/refs";

export const ATTR_LABELS: Record<string, string> = {
  target_role: "想找的方向", target_city: "想去的城市", job_type: "找什么类型", available_from: "最早什么时候能开始",
  days_per_week: "每周能到岗几天", duration_months: "能实习多久（月）", grad_year: "毕业年份", school: "学校", degree: "学历",
  major: "专业", skill: "会的技术", project: "做过的项目", company_like: "想去的公司", company_avoid: "不想去的公司或团队",
  applied: "投递过的岗位", interview_stage: "面试进度", salary_expect: "期望薪资", constraint: "其他硬条件",
};
const KEPT = new Set(["add", "update", "schedule", "confirm"]);
const ASKED = new Set(["pending", "ask"]);
const DROPPED = new Set(["invalidate", "reject", "forget"]);
const NO_REFS = new Map<string, string>();
const LIVE_TOOLS = new Set(["source_open", "platform_search"]);  // 真的去外面查的步骤，用强调色标出来

function MemoryLine({ e }: { e: MemoryEvent }) {
  const [state, setState] = useState<"idle" | "confirm" | "busy" | "gone" | "error">("idle");
  const label = e.label ?? ATTR_LABELS[e.attribute] ?? e.attribute;
  const value = hideRefs(e.value ?? "");
  const scope = e.scope_label ?? (e.qualifier ? hideRefs(e.qualifier) : "");
  async function forget() {
    setState("busy");
    try {
      const params = new URLSearchParams({ value: e.value ?? "", ...(e.qualifier ? { qualifier: e.qualifier } : {}) });
      await api(`memory/${encodeURIComponent(e.attribute)}?${params}`, { method: "DELETE" });
      setState("gone");
    } catch { setState("error"); }
  }
  const kept = KEPT.has(e.op);
  const verb = kept ? (e.op === "schedule" ? "记下了将来的安排" : "已记住") : ASKED.has(e.op) ? "待你确认" : "已作废";
  const Icon = kept ? Brain : ASKED.has(e.op) ? CircleHelp : Undo2;
  return (
    <motion.div initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }}
                className={`flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[13px] ${ASKED.has(e.op) ? "text-warn" : "text-muted"}`}>
      <Icon size={13} aria-hidden className="shrink-0" />
      <span className={state === "gone" ? "line-through" : ""}>
        {verb}：{label}{scope ? `（${scope}）` : ""} <span className="text-foreground">{value}</span>
        {e.op === "update" && e.old_value ? <span>（原来是 {hideRefs(e.old_value)}）</span> : null}
      </span>
      {kept && state === "idle" && <button type="button" onClick={() => setState("confirm")} className="text-faint hover:text-bad">删掉</button>}
      {state === "confirm" && <span className="inline-flex gap-2">
        <span>以后也不再这样推断，确定？</span>
        <button type="button" onClick={forget} className="text-bad">删掉</button>
        <button type="button" onClick={() => setState("idle")} className="text-faint hover:text-foreground">算了</button>
      </span>}
      {state === "busy" && <Loader2 size={12} className="animate-spin" aria-hidden />}
      {state === "gone" && <span className="text-faint">已删掉</span>}
      {state === "error" && <span className="text-bad">没删成，去记忆页再试</span>}
    </motion.div>
  );
}

export function MemoryNotes({ events }: { events: MemoryEvent[] }) {
  const shown = events.filter((e) => KEPT.has(e.op) || ASKED.has(e.op) || DROPPED.has(e.op));
  if (!shown.length) return null;
  return <div className="space-y-1">{shown.map((e, i) => <MemoryLine key={i} e={e} />)}</div>;
}

const FIELD_NAMES: Record<string, string> = {
  company: "公司", query: "想找什么", keyword: "关键词", role: "岗位", job_type: "岗位类型", material: "资料性质", source: "来源",
  limit: "条数", round: "轮次", post_type: "资料类型", city: "城市", direction: "方向", since_days: "最近几天", skill: "技能",
  of: "数什么", group_by: "按什么分组", attribute: "记忆项", qualifier: "范围", value: "内容", day: "哪天",
};

function StepRow({ step, sources, running }: { step: Step; sources?: string[]; running?: boolean }) {
  const args = Object.entries(step.args ?? {}).filter(([k, v]) => v != null && v !== "" && !["offset", "response_format", "big_only"].includes(k));
  return (
    <motion.li initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} className="relative pl-6">
      <span className="absolute left-0 top-[3px] grid place-items-center size-4">
        {!step.done ? <Loader2 size={13} className={running ? "animate-spin text-accent" : "text-faint"} aria-hidden />
          : step.ok === false ? <CircleAlert size={13} className="text-bad" aria-hidden />
          : <motion.span initial={{ scale: 0.4 }} animate={{ scale: 1 }}><Check size={13} className="text-ok" aria-hidden /></motion.span>}
      </span>
      {LIVE_TOOLS.has(step.tool) && <Globe size={12} className="inline -mt-0.5 mr-1 text-accent" aria-label="现查" />}
      <span className={LIVE_TOOLS.has(step.tool) ? "text-accent" : "text-foreground/90"}>{hideRefs(step.desc)}</span>
      {step.summary && <span className="text-muted"> · {step.summary}</span>}
      {!!sources?.length && <span className="text-faint"> · {sources.join("、")}</span>}
      {args.length > 0 && step.done && (
        <details className="mt-0.5">
          <summary className="cursor-pointer text-faint hover:text-muted text-xs list-none">查看筛选条件</summary>
          <dl className="mt-1 text-xs text-muted space-y-0.5">{args.map(([k, v]) => <div key={k}>
            <dt className="inline">{FIELD_NAMES[k] ?? k}：</dt><dd className="inline break-all">{hideRefs(String(v))}</dd>
          </div>)}</dl>
        </details>
      )}
    </motion.li>
  );
}

function memorySummary(events: MemoryEvent[] | null): string {
  if (!events) return "整理你这句话里的信息";
  const kept = events.filter((e) => KEPT.has(e.op)).length;
  const asked = events.filter((e) => ASKED.has(e.op)).length;
  if (!kept && !asked) return "未返回新增或待确认的记忆记录";
  return [kept ? `记住了 ${kept} 条` : "", asked ? `${asked} 条要问你` : ""].filter(Boolean).join("，");
}

// 回答完成后：折叠成一行"思考了 N 秒 · 查了 M 次"，点开看每一步
export function ThinkingDone({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState(false);
  const steps: (Step & { sources?: string[] })[] = turn.activities
    ? turn.activities.map((a) => ({ tool: a.tool, desc: a.desc ?? TOOL_NAMES[a.tool] ?? "处理资料", done: true, ok: a.ok,
                                    summary: a.summary ?? (a.returned != null ? `找到 ${a.returned} 条` : undefined),
                                    args: a.args, sources: a.sources }))
    : turn.tool_calls.map((c) => ({ tool: c.tool, desc: TOOL_NAMES[c.tool] ?? "处理资料", done: true, ok: c.ok, args: c.args }));
  const seconds = (turn.latency_ms / 1000).toFixed(1);
  if (!steps.length && !turn.memory_events.length && !turn.trace) return null;
  return (
    <div className="text-[13px]">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="inline-flex items-center gap-1 text-muted hover:text-foreground transition-colors">
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={{ duration: 0.15 }}><ChevronRight size={14} aria-hidden /></motion.span>
        思考了 {seconds} 秒{steps.length ? ` · 查了 ${steps.length} 次` : ""}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ol initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                     transition={{ duration: 0.2 }} className="overflow-hidden ml-[7px] mt-2 pl-3 border-l border-line space-y-1.5">
            <li className="relative pl-6 text-muted">
              <span className="absolute left-0 top-[3px]"><Brain size={13} aria-hidden /></span>
              记忆 · {memorySummary(turn.memory_events)}
            </li>
            {steps.map((s, i) => <StepRow key={i} step={s} sources={s.sources} />)}
            {turn.trace && (
              <li className="relative pl-6 pt-1">
                <details>
                  <summary className="cursor-pointer text-faint hover:text-muted text-xs list-none">查看追踪（每一步的耗时和 token）</summary>
                  <div className="mt-2 pr-1"><TraceTimeline trace={turn.trace} seconds={turn.latency_ms / 1000} /></div>
                </details>
              </li>
            )}
          </motion.ol>
        )}
      </AnimatePresence>
    </div>
  );
}

// 正在回答：一行会动的状态，下面是逐步展开的步骤和回答草稿
export function ThinkingLive({ live, elapsed, onStop }: { live: Live; elapsed: number; onStop: () => void }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-[13.5px]">
        <span className="live-dot shrink-0" aria-hidden />
        <span role="status" className="glint font-medium">{live.status}</span>
        <span className="text-faint tabular-nums">{Math.max(1, elapsed)} 秒</span>
        {!live.stopping && live.status !== "消息已收到，正在排队" && (
          <button type="button" onClick={onStop} className="ml-auto inline-flex items-center gap-1 text-xs text-muted hover:text-bad">
            <Square size={11} aria-hidden />停止
          </button>
        )}
      </div>
      {(live.steps.length > 0 || live.memory !== null) && (
        <ol className="ml-[3px] pl-3 border-l border-line space-y-1.5 text-[13px]">
          <li className="relative pl-6 text-muted">
            <span className="absolute left-0 top-[3px]">
              {live.memory ? <Brain size={13} aria-hidden /> : <Loader2 size={13} className="animate-spin text-accent" aria-hidden />}
            </span>
            记忆 · {memorySummary(live.memory)}
          </li>
          {live.steps.map((s, i) => <StepRow key={i} step={s} running />)}
        </ol>
      )}
      {live.memory && <MemoryNotes events={live.memory} />}
      {live.draft && <Markdown text={live.draft} streaming refs={NO_REFS} />}
    </div>
  );
}

export function CompactDivider({ info }: { info: Compacted }) {
  const [open, setOpen] = useState(false);
  const saved = info.before_tokens > 0 ? Math.max(0, Math.round((1 - info.after_tokens / info.before_tokens) * 100)) : 0;
  return (
    <div className="my-2">
      <div className="flex items-center gap-3 text-xs text-faint">
        <span className="flex-1 border-t border-dashed border-line" />
        <span className="inline-flex items-center gap-1.5">
          <Archive size={12} aria-hidden />前面 {info.messages} 条消息已压缩成摘要{saved ? `，省下约 ${saved}% 的上下文` : ""}
          <button type="button" onClick={() => setOpen(!open)} className="text-accent hover:underline">{open ? "收起" : "看摘要"}</button>
        </span>
        <span className="flex-1 border-t border-dashed border-line" />
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                      className="overflow-hidden">
            <div className="mt-2 rounded-lg bg-soft px-4 py-3 text-[13px] text-muted">
              <p className="text-xs text-faint mb-1">之后北极星看到的是这份摘要和最近几条消息；你的聊天记录原样保留。</p>
              <Markdown text={info.summary} refs={NO_REFS} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ContextMeter({ context }: { context?: { tokens: number; limit: number } }) {
  if (!context) return null;
  const pct = Math.min(100, Math.round((context.tokens / context.limit) * 100));
  const r = 7;
  const c = 2 * Math.PI * r;
  const color = pct >= 85 ? "var(--warn)" : "var(--faint)";
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-faint tabular-nums"
          title={`上下文用了 ${pct}%（约 ${context.tokens} / ${context.limit} token）。满了会自动把前面的对话压缩成摘要，聊天记录不受影响。`}>
      <svg width="18" height="18" className="-rotate-90" aria-hidden>
        <circle cx="9" cy="9" r={r} fill="none" stroke="var(--line)" strokeWidth="2.5" />
        <motion.circle cx="9" cy="9" r={r} fill="none" stroke={color} strokeWidth="2.5" strokeDasharray={c}
                       initial={false} animate={{ strokeDashoffset: c * (1 - pct / 100) }} transition={{ duration: 0.6 }} />
      </svg>
      上下文 {pct}%
    </span>
  );
}

