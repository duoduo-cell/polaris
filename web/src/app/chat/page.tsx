"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowUp, BookOpen, Briefcase, Check, ChevronDown, Copy, Flag, GraduationCap, Plus, ShieldCheck, Square, ThumbsDown, ThumbsUp,
} from "lucide-react";
import { Badge, Button, ErrorNote } from "@/components/markdown";
import { ResultCards } from "@/components/result-cards";
import { type Compacted, type Task, TOOL_NAMES, useChatSession } from "@/components/chat-session";
import { CompactDivider, ContextMeter, MemoryNotes, ThinkingDone, ThinkingLive } from "@/components/chat-thinking";
import { hideRefs } from "@/lib/refs";

// 审批卡片上参数的中文名
const APPROVAL_ARGS: Record<string, string> = {
  attribute: "记忆项", qualifier: "范围", value: "内容", title: "名字", city: "城市", job_type: "类型", direction: "方向",
  max_days_per_week: "每周最多天数", big_only: "只看大厂", job_ids: "重点岗位", days: "盯多少天", cadence_minutes: "检查间隔（分钟）",
};

const SUGGESTIONS = [
  "我在北京，想找 Agent 开发的日常实习，每周能去 4 天，能实习 3 个月",
  "按我的条件推荐 3 个岗位，说说哪里合适、哪里不合适",
  "腾讯和字节，哪家要求 LangGraph 的岗位多？",
  "字节面试问过哪些 Agent 记忆设计的问题？",
];
const MODES = [
  { label: "找岗位", icon: Briefcase, text: "按我的条件推荐 3 个岗位，说说哪里合适、哪里不合适" },
  { label: "查面经", icon: BookOpen, text: "最近有哪些 Agent 方向的面经？重点看问了什么：" },
  { label: "备面试", icon: GraduationCap, text: "帮我准备面试：常考什么、我还缺什么。岗位是：" },
  { label: "记进度", icon: Flag, text: "更新一下进度：我" },
];

function TaskNote({ task, stop }: { task: Task; stop: (id: string) => void }) {
  if (task.state === "queued") {
    return <p className="text-xs text-faint text-right">排队中，前面的回答完就开始 · <button type="button" className="hover:text-bad" onClick={() => stop(task.id)}>取消</button></p>;
  }
  if (task.state === "cancelled") return <p className="text-xs text-faint text-right">已停止{task.error ? ` · ${task.error}` : ""}</p>;
  if (task.state === "waiting_approval" && task.cancel_requested) {
    return <p className="text-xs text-faint text-right">回答已停止，待确认动作仍需你批准或拒绝。</p>;
  }
  if (task.error && ["failed", "interrupted"].includes(task.state)) return <ErrorNote error={task.error} />;
  return null;
}

export default function Chat() {
  const { thread, threads, messages, approvals, tasks, input, busy, submitting, loading, live, elapsed, error,
          setInput, newThread, switchThread, send, decide, mark: markAnswer, stop } = useChatSession();
  const scroller = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);
  const [copied, setCopied] = useState<number | null>(null);
  const liveKey = live ? `${live.status}|${live.steps.length}|${live.draft.length}|${live.memory?.length ?? -1}` : "";

  useEffect(() => {  // 在底部附近才跟着滚；往上翻着看的时候不打扰
    if (stick.current) end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, approvals, liveKey]);
  useEffect(() => {  // 输入框随内容长高
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 200)}px`; }
  }, [input]);

  // 测试时用：给一条回答标"对 / 有问题"，存在本机，开发时逐条看
  async function mark(index: number, verdict: "good" | "bad") {
    const note = verdict === "bad" ? window.prompt("哪里有问题？（越具体越好：应该怎样、实际怎样）") : "";
    if (note === null) return;
    await markAnswer(index, verdict, note ?? "");
  }
  async function copy(index: number, text: string) {
    try { await navigator.clipboard.writeText(hideRefs(text)); setCopied(index); setTimeout(() => setCopied(null), 1500); } catch { /* 浏览器不让写剪贴板 */ }
  }
  function fill(text: string) {
    setInput(text);
    requestAnimationFrame(() => { const el = box.current; if (el) { el.focus(); el.setSelectionRange(text.length, text.length); } });
  }

  // 压缩发生在某次回答前：分隔线画在引出那次回答的那句话上面
  const dividers = new Map<number, Compacted>();
  messages.forEach((m, i) => {
    if (m.role !== "assistant" || !m.turn?.compacted) return;
    for (let j = i - 1; j >= 0; j--) if (messages[j].role === "user") { dividers.set(j, m.turn.compacted); break; }
  });
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const shownTasks = new Set(messages.map((m) => m.task_id).filter(Boolean));
  const orphanErrors = tasks.filter((t) => t.error && ["failed", "interrupted"].includes(t.state) && !shownTasks.has(t.id));
  const context = [...messages].reverse().find((m) => m.turn?.context)?.turn?.context;
  const resumeRunning = tasks.some((t) => t.kind === "resume" && ["queued", "running"].includes(t.state));
  const blocked = submitting || loading || approvals.length > 0;

  return (
    <div className="chat-h flex flex-col">
      <div className="mx-auto w-full max-w-3xl flex items-center justify-between gap-3 pb-3">
        <label className="relative inline-flex items-center min-w-0">
          <select aria-label="切换对话" value={thread} onChange={(e) => switchThread(e.target.value)} disabled={loading || submitting}
                  className="appearance-none bg-transparent pr-6 text-sm font-medium truncate max-w-[16rem] outline-none cursor-pointer disabled:cursor-default">
            {!threads.some((t) => t.id === thread) && <option value={thread}>新对话</option>}
            {threads.map((t) => <option key={t.id} value={t.id}>{t.active ? "处理中 · " : t.waiting_approval ? "待批准 · " : ""}{hideRefs(t.title)}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-1 text-muted pointer-events-none" aria-hidden />
        </label>
        <Button variant="ghost" icon={Plus} onClick={newThread} disabled={submitting || loading} className="whitespace-nowrap shrink-0">新对话</Button>
      </div>

      <div ref={scroller} data-testid="chat-transcript" className="flex-1 overflow-y-auto -mx-4 px-4"
           onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140; }}>
        <div className="mx-auto w-full max-w-3xl space-y-7 pb-8 pt-2">
          {messages.length === 0 && !busy && !loading && (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="pt-[8vh]">
              <h2 className="text-2xl font-semibold tracking-tight text-center">今天想找点什么？</h2>
              <p className="text-sm text-muted text-center mt-2">说说方向、城市、每周能去几天——北极星会记住，之后推荐、推送都按这个来。</p>
              <div className="mt-8 grid sm:grid-cols-2 gap-2">
                {SUGGESTIONS.map((s, i) => (
                  <motion.button key={s} type="button" onClick={() => send(s)}
                                 initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i + 0.1 }}
                                 className="text-left text-sm rounded-xl border border-line bg-card px-4 py-3 text-muted hover:text-foreground hover:border-faint transition-colors">
                    {s}
                  </motion.button>
                ))}
              </div>
            </motion.div>
          )}

          <AnimatePresence initial={false}>
            {messages.map((m, i) => {
              const task = m.task_id ? byId.get(m.task_id) : undefined;
              return (
                <motion.div key={m.id ?? i} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22 }}>
                  {dividers.has(i) && <CompactDivider info={dividers.get(i)!} />}
                  {m.role === "user" ? (
                    <div className="space-y-1.5">
                      <div className="flex justify-end">
                        <div className="rounded-2xl bg-soft px-4 py-2.5 max-w-[85%] whitespace-pre-wrap leading-relaxed">{m.content}</div>
                      </div>
                      {task && <TaskNote task={task} stop={stop} />}
                    </div>
                  ) : (
                    <div className="group space-y-3">
                      {m.turn && <ThinkingDone turn={m.turn} />}
                      {m.turn && <MemoryNotes events={m.turn.memory_events} />}
                      {m.turn?.error && (
                        <div role="status" className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn">
                          <p>本轮有步骤未完成，已生成的回答和结果已保留。</p>
                          <details className="mt-1 text-xs"><summary className="cursor-pointer">查看原因</summary>{m.turn.error}</details>
                        </div>
                      )}
                      {m.content && <ResultCards text={m.content} results={m.turn?.results ?? []} fold />}
                      {m.content && (
                        <div className="flex items-center gap-0.5 text-muted">
                          <Button variant="ghost" icon={copied === i ? Check : Copy} label="复制回答" title="复制" className="!px-1.5 !py-1"
                                  onClick={() => copy(i, m.content)} />
                          {m.verdict ? (
                            <Badge tone={m.verdict === "good" ? "green" : "amber"} className="ml-1">{m.verdict === "good" ? "你标了：对" : "你标了：有问题"}</Badge>
                          ) : (
                            <>
                              <Button variant="ghost" icon={ThumbsUp} onClick={() => mark(i, "good")} className="!px-1.5 !py-1 text-xs" title="这条回答是对的">对</Button>
                              <Button variant="ghost" icon={ThumbsDown} onClick={() => mark(i, "bad")} className="!px-1.5 !py-1 text-xs" title="这条回答有问题">有问题</Button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>

          <AnimatePresence>
            {live && (
              <motion.div key="live" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <ThinkingLive live={live} elapsed={elapsed} onStop={() => stop(live.taskId)} />
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {approvals.map((a, i) => (
              <motion.div key={`ap-${i}`} initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
                          className="rounded-xl border border-warn/40 bg-warn-soft p-4 space-y-3">
                <div className="flex items-center gap-2 font-medium"><ShieldCheck size={17} className="text-warn" aria-hidden />
                  北极星想执行：{TOOL_NAMES[a.name] ?? "修改你的数据"}</div>
                <p className="text-sm text-muted">{a.description}</p>
                <dl className="text-sm rounded-lg bg-card px-3 py-2 space-y-1">
                  {Object.entries(a.args).filter(([, value]) => value !== null).map(([key, value]) => (
                    <div key={key}><dt className="inline text-muted">{APPROVAL_ARGS[key] ?? key}：</dt>
                      <dd className="inline">{Array.isArray(value) ? `${value.length} 个（本轮查到的岗位）` : typeof value === "boolean" ? (value ? "是" : "否")
                        : hideRefs(key === "job_type" ? ({ intern: "实习", campus: "校招", social: "社招" }[String(value)] ?? String(value)) : String(value))}</dd></div>
                  ))}
                </dl>
                <div className="flex gap-2">
                  <Button variant="primary" onClick={() => decide(true)} disabled={submitting || resumeRunning} icon={Check}>批准</Button>
                  <Button onClick={() => decide(false)} disabled={submitting || resumeRunning}>拒绝</Button>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
          {orphanErrors.map((t) => <ErrorNote key={t.id} error={t.error} />)}
          <ErrorNote error={error} />
          <div ref={end} />
        </div>
      </div>

      <div className="mx-auto w-full max-w-3xl pt-2">
        <div className="rounded-2xl border border-line bg-card shadow-[0_1px_3px_rgba(0,0,0,0.04)] focus-within:border-faint transition-colors">
          <textarea
            ref={box}
            className="block w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[15px] leading-relaxed outline-none placeholder:text-faint"
            rows={1}
            placeholder={approvals.length ? "先批准或拒绝上面的动作" : busy ? "可以接着说，会按顺序处理" : "交给北极星一件事，或者问点什么"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
            disabled={blocked}
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5 pt-1">
            <div className="flex flex-wrap gap-1">
              {MODES.map(({ label, icon: Icon, text }) => (
                <button key={label} type="button" onClick={() => fill(text)} disabled={blocked}
                        className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted hover:bg-soft hover:text-foreground disabled:opacity-40 transition-colors">
                  <Icon size={13} aria-hidden />{label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <ContextMeter context={context} />
              {live && !live.stopping && (
                <motion.button initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} type="button" aria-label="停止回答"
                               title="停止回答" onClick={() => stop(live.taskId)}
                               className="grid place-items-center size-8 rounded-lg border border-line text-foreground hover:bg-soft">
                  <Square size={12} fill="currentColor" aria-hidden />
                </motion.button>
              )}
              <motion.button whileTap={{ scale: 0.92 }} type="button" aria-label="发送" onClick={() => send()} disabled={blocked || !input.trim()}
                             className="grid place-items-center size-8 rounded-lg bg-foreground text-background disabled:opacity-25 transition-opacity">
                <ArrowUp size={16} aria-hidden />
              </motion.button>
            </div>
          </div>
        </div>
        <p className="text-center text-[11px] text-faint mt-2">北极星只读你的数据；投递、发消息都由你自己来。</p>
      </div>
    </div>
  );
}
