"use client";

import { useEffect, useRef, useState } from "react";
import { Badge, Card, ErrorNote, Markdown } from "@/components/markdown";
import { api, stream } from "@/lib/client";

type Approval = { name: string; args: Record<string, unknown>; description: string };
type MemoryEvent = { op: string; attribute: string; value: string | null; reason: string };
type Turn = {
  answer: string;
  memory_events: MemoryEvent[];
  memory_questions: string[];
  approvals: Approval[];
  tool_calls: { tool: string; args: Record<string, unknown>; ok: boolean }[];
  latency_ms: number;
  error: string | null;
};
type Message = { role: "user" | "assistant"; content: string; turn?: Turn; verdict?: "good" | "bad" };

const SHOWN_OPS = new Set(["add", "update", "schedule", "invalidate", "pending", "ask", "confirm", "reject", "forget"]);
const OP_NAMES: Record<string, string> = {
  add: "记下", update: "更新", schedule: "记下将来的安排", invalidate: "作废", pending: "待你确认", ask: "要问你",
  confirm: "确认", reject: "作废待确认的", forget: "忘掉",
};

export default function Chat() {
  // 当前会话编号记在浏览器里；"新对话"换一个编号（记忆是所有会话共用的，换会话只是清掉聊天记录）
  const [thread, setThread] = useState(() => {
    if (typeof window === "undefined") return "main";
    try {
      return localStorage.getItem("polaris-thread") ?? "main";
    } catch {
      return "main";  // 浏览器不让存就用默认的
    }
  });
  function newThread() {
    const id = `t-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}`;
    try { localStorage.setItem("polaris-thread", id); } catch { /* 忽略 */ }
    setMessages([]); setApprovals([]); setThread(id);
  }
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<{ memory: MemoryEvent[]; tool: string | null; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ messages: Message[]; approvals: Approval[] }>(`chat/${thread}/messages`)
      .then((r) => { setMessages(r.messages); setApprovals(r.approvals); })
      .catch((e) => setError(e.message));
  }, [thread]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });  // 新版 Chromium 的 scrollIntoView 返回 Promise，不能直接当 effect 的返回值
  }, [messages, approvals, busy]);

  // 测试时用：给一条回答标"对 / 有问题"，存在本机，开发时逐条看
  async function mark(index: number, verdict: "good" | "bad") {
    const note = verdict === "bad" ? window.prompt("哪里有问题？（越具体越好：应该怎样、实际怎样）") : "";
    if (note === null) return;
    const question = [...messages.slice(0, index)].reverse().find((m) => m.role === "user")?.content ?? "";
    try {
      await api("feedback", { body: { thread_id: thread, question, answer: messages[index].content, verdict, note } });
      setMessages((ms) => ms.map((m, i) => (i === index ? { ...m, verdict } : m)));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function take(turn: Turn) {
    if (turn.error) setError(turn.error);
    setApprovals(turn.approvals);
    if (turn.answer || turn.memory_events.length || turn.tool_calls.length) {
      setMessages((m) => [...m, { role: "assistant", content: turn.answer, turn }]);
    }
  }

  // 流式：记忆改了什么、正在调哪个工具、回答的字，边收边显示；最后用 done 里的完整结果替换
  async function run(path: string, body: unknown) {
    setBusy(true); setError(null); setLive({ memory: [], tool: null, text: "" });
    try {
      await stream(path, body, (e) => {
        if (e.type === "memory") setLive((l) => l && { ...l, memory: e.events as MemoryEvent[] });
        else if (e.type === "tool") setLive((l) => l && { ...l, tool: e.tool as string, text: "" });
        else if (e.type === "token") setLive((l) => l && { ...l, text: l.text + (e.text as string) });
        else if (e.type === "done") take(e.turn as Turn);
        else if (e.type === "error") setError(e.message as string);
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLive(null); setBusy(false);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    await run("chat/stream", { message: text, thread_id: thread });
  }

  async function decide(approve: boolean) {
    await run(`chat/${thread}/resume/stream`, { approve, reason: approve ? "" : "我没有批准" });
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-bold">对话</h1>
        <div className="flex items-center gap-2 text-sm text-muted">
          <span suppressHydrationWarning>会话 {thread}</span>
          <button className="rounded border border-line px-2 py-0.5 hover:bg-card disabled:opacity-50" onClick={newThread} disabled={busy}>新对话</button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {messages.length === 0 && (
          <p className="text-sm text-muted">跟北极星说说你想找什么样的实习（方向、城市、每周能去几天、能实习多久），它会记下来，之后推荐和推送都按这个来。</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            {m.role === "user" ? (
              <div className="rounded-lg bg-accent text-white px-3 py-2 max-w-[80%] whitespace-pre-wrap">{m.content}</div>
            ) : (
              <Card className="space-y-2">
                {m.turn && m.turn.memory_events.filter((e) => SHOWN_OPS.has(e.op)).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {m.turn.memory_events.filter((e) => SHOWN_OPS.has(e.op)).map((e, j) => (
                      <Badge key={j} tone={e.op === "ask" || e.op === "pending" ? "amber" : "green"}>
                        记忆 · {OP_NAMES[e.op] ?? e.op}：{e.value}
                      </Badge>
                    ))}
                  </div>
                )}
                {m.turn && m.turn.tool_calls.length > 0 && (
                  <details className="text-xs text-muted">
                    <summary className="cursor-pointer">查了 {m.turn.tool_calls.length} 次（{(m.turn.latency_ms / 1000).toFixed(1)} 秒）</summary>
                    <ul className="mt-1 space-y-0.5">
                      {m.turn.tool_calls.map((c, j) => (
                        <li key={j} className="font-mono break-all">{c.ok ? "✓" : "✗"} {c.tool} {JSON.stringify(c.args)}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {m.content && <Markdown text={m.content} />}
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
            <p className="font-medium">北极星想执行：{a.name}</p>
            <p className="text-sm text-muted">{a.description}</p>
            <pre className="text-xs bg-background rounded p-2 overflow-x-auto">{JSON.stringify(a.args, null, 2)}</pre>
            <div className="flex gap-2">
              <button disabled={busy} className="rounded bg-accent text-white px-3 py-1 text-sm disabled:opacity-50" onClick={() => decide(true)}>批准</button>
              <button disabled={busy} className="rounded border border-line px-3 py-1 text-sm disabled:opacity-50" onClick={() => decide(false)}>拒绝</button>
            </div>
          </Card>
        ))}
        {live && (
          <Card className="space-y-2">
            {live.memory.filter((e) => SHOWN_OPS.has(e.op)).length > 0 && (
              <div className="flex flex-wrap gap-1">
                {live.memory.filter((e) => SHOWN_OPS.has(e.op)).map((e, j) => (
                  <Badge key={j} tone="green">记忆 · {OP_NAMES[e.op] ?? e.op}：{e.value}</Badge>
                ))}
              </div>
            )}
            {live.text ? <Markdown text={live.text} /> : (
              <p className="text-sm text-muted">{live.tool ? `正在查：${live.tool}……` : "北极星在想……"}</p>
            )}
          </Card>
        )}
        <ErrorNote error={error} />
        <div ref={end} />
      </div>
      <div className="mt-3 flex gap-2">
        <textarea
          className="flex-1 rounded-lg border border-line bg-card px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder="比如：我在北京，想找 Agent 开发的日常实习，每周能去 4 天"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
          disabled={busy || approvals.length > 0}
        />
        <button className="rounded-lg bg-accent text-white px-4 text-sm disabled:opacity-50" onClick={send} disabled={busy || approvals.length > 0 || !input.trim()}>
          发送
        </button>
      </div>
    </div>
  );
}
