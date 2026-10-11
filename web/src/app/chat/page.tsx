"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowUp, Check, Loader2, MessageCircle, Plus, ShieldCheck, Sparkles, ThumbsDown, ThumbsUp, User,
} from "lucide-react";
import { Badge, Button, Card, ErrorNote, PageHeader } from "@/components/markdown";
import { ResultCards } from "@/components/result-cards";
import { useChatSession } from "@/components/chat-session";
import { ChatActivity, TOOL_NAMES } from "@/components/chat-activity";

const SUGGESTIONS = [
  "我在北京，想找 Agent 开发的日常实习，每周能去 4 天",
  "按我的条件推荐 3 个岗位，说说为什么",
  "腾讯和字节，哪家要求 LangGraph 的岗位多？",
  "字节面试问过哪些 Agent 记忆设计的问题？",
];

function Avatar({ role }: { role: "user" | "assistant" }) {
  return role === "assistant" ? (
    <div className="grid place-items-center size-8 shrink-0 rounded-xl bg-gradient-to-br from-accent to-accent-2 text-white shadow-sm">
      <Sparkles size={15} />
    </div>
  ) : (
    <div className="grid place-items-center size-8 shrink-0 rounded-xl bg-soft text-muted"><User size={15} /></div>
  );
}

export default function Chat() {
  const { thread, threads, messages, approvals, tasks, input, busy, submitting, loading, live, elapsed, error,
          setInput, newThread, switchThread, send, decide, mark: markAnswer } = useChatSession();
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });  // 新版 Chromium 的 scrollIntoView 返回 Promise，不能直接当 effect 的返回值
  }, [messages, approvals, live]);
  useEffect(() => {  // 输入框随内容长高
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 180)}px`; }
  }, [input]);

  // 测试时用：给一条回答标"对 / 有问题"，存在本机，开发时逐条看
  async function mark(index: number, verdict: "good" | "bad") {
    const note = verdict === "bad" ? window.prompt("哪里有问题？（越具体越好：应该怎样、实际怎样）") : "";
    if (note === null) return;
    await markAnswer(index, verdict, note ?? "");
  }

  const blocked = submitting || loading || approvals.length > 0;
  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <PageHeader icon={MessageCircle} title="对话" right={
        <div className="flex items-center gap-2">
          <select aria-label="切换对话" value={thread} onChange={(e) => switchThread(e.target.value)} disabled={loading || submitting}
                  className="text-xs text-muted bg-transparent max-w-40 outline-none">
            {!threads.some((t) => t.id === thread) && <option value={thread}>当前对话</option>}
            {threads.map((t) => <option key={t.id} value={t.id}>{t.active ? "处理中 · " : t.waiting_approval ? "待批准 · " : ""}{t.title}</option>)}
          </select>
          <Button variant="outline" icon={Plus} onClick={newThread} disabled={submitting || loading}>新对话</Button>
        </div>
      }>
        告诉北极星你的需求，它会记住你的条件、查找资料；需要改动数据时会先请你批准
      </PageHeader>

      <div data-testid="chat-transcript" className="flex-1 overflow-y-auto space-y-5 pr-1 pb-4">
        {messages.length === 0 && !busy && (
          <div className="pt-6">
            <div className="text-center mb-6">
              <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                          className="inline-grid place-items-center size-14 rounded-2xl bg-gradient-to-br from-accent to-accent-2 text-white shadow-lg mb-3">
                <Sparkles size={26} />
              </motion.div>
              <p className="text-lg font-semibold">跟北极星说说你想找什么样的实习</p>
              <p className="text-sm text-muted mt-1">方向、城市、每周能去几天、能实习多久——它会记下来，之后推荐和推送都按这个来</p>
            </div>
            <div className="grid sm:grid-cols-2 gap-2.5 max-w-2xl mx-auto">
              {SUGGESTIONS.map((s, i) => (
                <motion.button key={s} onClick={() => send(s)}
                               initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i }}
                               whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }}
                               className="text-left text-sm rounded-2xl border border-line bg-card px-4 py-3 hover:border-accent/50 hover:shadow-md transition-shadow">
                  {s}
                </motion.button>
              ))}
            </div>
          </div>
        )}

        <AnimatePresence initial={false}>
          {messages.map((m, i) => (
            <motion.div key={m.id ?? i} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}
                        className={`flex gap-3 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
              <Avatar role={m.role} />
              {m.role === "user" ? (
                <div className="rounded-2xl rounded-tr-md bg-gradient-to-r from-accent to-accent-2 text-white px-4 py-2.5 max-w-[78%] whitespace-pre-wrap shadow-sm">
                  {m.content}
                </div>
              ) : (
                <div className="flex-1 min-w-0 rounded-2xl rounded-tl-md border border-line bg-card px-4 py-3 space-y-2.5 shadow-sm">
                  {m.turn && <ChatActivity turn={m.turn} />}
                  {m.content && <ResultCards text={m.content} results={m.turn?.results ?? []} />}
                  {m.content && (
                    <div className="flex items-center gap-1 pt-1">
                      {m.verdict ? (
                        <Badge tone={m.verdict === "good" ? "green" : "amber"}>{m.verdict === "good" ? "你标了：对" : "你标了：有问题"}</Badge>
                      ) : (
                        <>
                          <Button variant="ghost" icon={ThumbsUp} onClick={() => mark(i, "good")} className="!px-2 !py-1 text-xs">对</Button>
                          <Button variant="ghost" icon={ThumbsDown} onClick={() => mark(i, "bad")} className="!px-2 !py-1 text-xs">有问题</Button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>

        <AnimatePresence>
          {live && (
            <motion.div key="live" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex gap-3">
              <Avatar role="assistant" />
              <div className="flex-1 min-w-0 rounded-2xl rounded-tl-md border border-accent/30 bg-card px-4 py-3 space-y-3 shadow-sm">
                <div className="flex items-center gap-2 text-sm text-muted" role="status">
                  <Loader2 size={14} className="animate-spin text-accent" />
                  {live.status}
                </div>
                <ChatActivity activities={(tasks.find((t) => t.state === "running") ?? tasks.find((t) => t.state === "queued"))?.activities} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {approvals.map((a, i) => (
            <motion.div key={`ap-${i}`} initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}>
              <Card className="border-amber-400/50 bg-amber-500/5 space-y-3">
                <div className="flex items-center gap-2 font-medium"><ShieldCheck size={18} className="text-amber-500" />北极星想执行：{TOOL_NAMES[a.name] ?? "修改你的数据"}</div>
                <p className="text-sm text-muted">{a.description}</p>
                <dl className="text-sm bg-soft rounded-xl p-3 space-y-1">
                  {Object.entries(a.args).filter(([, value]) => value !== null).map(([key, value]) => (
                    <div key={key}><dt className="inline text-muted">{{ attribute: "记忆项", qualifier: "范围", value: "内容" }[key] ?? key}：</dt>
                      <dd className="inline">{String(value)}</dd></div>
                  ))}
                </dl>
                <div className="flex gap-2">
                  <Button onClick={() => decide(true)} disabled={submitting || tasks.some((t) => t.kind === "resume" && ["queued", "running"].includes(t.state))} icon={Check}>批准</Button>
                  <Button variant="outline" onClick={() => decide(false)} disabled={submitting || tasks.some((t) => t.kind === "resume" && ["queued", "running"].includes(t.state))}>拒绝</Button>
                </div>
              </Card>
            </motion.div>
          ))}
        </AnimatePresence>
        {tasks.filter((t) => t.error).map((t) => <ErrorNote key={t.id} error={t.error} />)}
        <ErrorNote error={error} />
        <div ref={end} />
      </div>

      <div className="relative mt-2 rounded-2xl border border-line bg-card shadow-sm focus-within:border-accent/60 focus-within:shadow-md transition">
        <textarea
          ref={box}
          className="block w-full resize-none bg-transparent px-4 pt-3 pb-11 text-sm outline-none placeholder:text-muted"
          rows={1}
          placeholder={approvals.length ? "先批准或拒绝上面的动作" : busy ? "可以继续说，消息会按顺序处理" : "说点什么……（Enter 发送，Shift+Enter 换行）"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
          disabled={blocked}
        />
        <div className="absolute bottom-2 right-2 left-4 flex items-center justify-between">
          <span className="text-xs text-muted">{busy ? `已经 ${Math.max(1, elapsed)} 秒` : ""}</span>
          <motion.button whileTap={{ scale: 0.9 }} onClick={() => send()} disabled={blocked || !input.trim()}
                         className="grid place-items-center size-8 rounded-xl bg-gradient-to-r from-accent to-accent-2 text-white disabled:opacity-30 transition">
            {submitting ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} />}
          </motion.button>
        </div>
      </div>
    </div>
  );
}
