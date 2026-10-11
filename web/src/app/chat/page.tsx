"use client";

import { useEffect, useRef } from "react";
import { Badge, Card, ErrorNote } from "@/components/markdown";
import { ResultCards } from "@/components/result-cards";
import { useChatSession } from "@/components/chat-session";
import { ChatActivity, TOOL_NAMES } from "@/components/chat-activity";

export default function Chat() {
  const { thread, threads, messages, approvals, tasks, input, submitting, loading, live, error,
          setInput, newThread, switchThread, send, decide, mark: markAnswer } = useChatSession();
  const busy = submitting || loading;
  const resuming = tasks.some((t) => t.kind === "resume" && ["queued", "running"].includes(t.state));
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, approvals, busy, live]);

  async function mark(index: number, verdict: "good" | "bad") {
    const note = verdict === "bad" ? window.prompt("哪里有问题？（越具体越好：应该怎样、实际怎样）") : "";
    if (note === null) return;
    await markAnswer(index, verdict, note ?? "");
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-bold">对话</h1>
        <div className="flex items-center gap-2 text-sm text-muted">
          <select aria-label="切换对话" value={thread} onChange={(e) => switchThread(e.target.value)} disabled={busy}
                  className="text-xs text-muted bg-transparent max-w-40 outline-none">
            {!threads.some((t) => t.id === thread) && <option value={thread}>当前对话</option>}
            {threads.map((t) => <option key={t.id} value={t.id}>{t.active ? "处理中 · " : t.waiting_approval ? "待批准 · " : ""}{t.title}</option>)}
          </select>
          <button className="rounded border border-line px-2 py-0.5 hover:bg-card disabled:opacity-50" onClick={newThread} disabled={busy}>新对话</button>
        </div>
      </div>
      <div data-testid="chat-transcript" className="flex-1 overflow-y-auto space-y-3 pr-1">
        {messages.length === 0 && (
          <p className="text-sm text-muted">跟北极星说说你想找什么样的实习（方向、城市、每周能去几天、能实习多久），它会记下来，之后推荐和推送都按这个来。</p>
        )}
        {messages.map((m, i) => (
          <div key={m.id ?? i} className={m.role === "user" ? "flex justify-end" : ""}>
            {m.role === "user" ? (
              <div className="rounded-lg bg-accent text-white px-3 py-2 max-w-[80%] whitespace-pre-wrap">{m.content}</div>
            ) : (
              <Card className="space-y-2">
                {m.turn && <ChatActivity turn={m.turn} />}
                {m.content && <ResultCards text={m.content} results={m.turn?.results ?? []} />}
                {m.content && (
                  <div className="flex gap-2 pt-1 text-xs">
                    {m.verdict ? (
                      <Badge tone={m.verdict === "good" ? "green" : "amber"}>{m.verdict === "good" ? "你标了：对" : "你标了：有问题"}</Badge>
                    ) : (
                      <>
                        <button className="rounded border border-line px-2 py-0.5 hover:bg-background" onClick={() => mark(i, "good")}>对</button>
                        <button className="rounded border border-line px-2 py-0.5 hover:bg-background" onClick={() => mark(i, "bad")}>有问题</button>
                      </>
                    )}
                  </div>
                )}
              </Card>
            )}
          </div>
        ))}
        {approvals.map((a, i) => (
          <Card key={i} className="border-amber-300 space-y-2">
            <p className="font-medium">北极星想执行：{TOOL_NAMES[a.name] ?? "修改你的数据"}</p>
            <p className="text-sm text-muted">{a.description}</p>
            <dl className="text-sm bg-background rounded p-2">
              {Object.entries(a.args).filter(([, value]) => value !== null).map(([key, value]) => (
                <div key={key}><dt className="inline text-muted">{{ attribute: "记忆项", qualifier: "范围", value: "内容" }[key] ?? key}：</dt>
                  <dd className="inline">{String(value)}</dd></div>
              ))}
            </dl>
            <div className="flex gap-2">
              <button disabled={busy || resuming} className="rounded bg-accent text-white px-3 py-1 text-sm disabled:opacity-50" onClick={() => decide(true)}>批准</button>
              <button disabled={busy || resuming} className="rounded border border-line px-3 py-1 text-sm disabled:opacity-50" onClick={() => decide(false)}>拒绝</button>
            </div>
          </Card>
        ))}
        {live && (
          <Card className="space-y-2">
            <p className="text-sm text-muted" role="status">{live.status}</p>
            <ChatActivity activities={(tasks.find((t) => t.state === "running") ?? tasks.find((t) => t.state === "queued"))?.activities} />
          </Card>
        )}
        {tasks.filter((t) => t.error).map((t) => <ErrorNote key={t.id} error={t.error} />)}
        <ErrorNote error={error} />
        <div ref={end} />
      </div>
      <div className="mt-3 flex gap-2">
        <textarea
          className="flex-1 rounded-lg border border-line bg-card px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder={approvals.length ? "先批准或拒绝上面的动作" : live ? "可以继续说，消息会按顺序处理" : "比如：我在北京，想找 Agent 开发的日常实习，每周能去 4 天"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
          disabled={busy || approvals.length > 0}
        />
        <button className="rounded-lg bg-accent text-white px-4 text-sm disabled:opacity-50" onClick={() => send()} disabled={busy || approvals.length > 0 || !input.trim()}>
          发送
        </button>
      </div>
    </div>
  );
}
