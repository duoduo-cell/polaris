"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api, stream } from "@/lib/client";

export type Approval = { name: string; args: Record<string, unknown>; description: string };
export type MemoryEvent = { op: string; attribute: string; value: string | null; reason: string };
export type Turn = {
  answer: string;
  memory_events: MemoryEvent[];
  memory_questions: string[];
  approvals: Approval[];
  tool_calls: { tool: string; args: Record<string, unknown>; ok: boolean }[];
  latency_ms: number;
  error: string | null;
};
export type Message = { role: "user" | "assistant"; content: string; turn?: Turn; verdict?: "good" | "bad" };
type Live = { memory: MemoryEvent[] | null; steps: { tool: string; done: boolean }[]; text: string };
type Session = {
  thread: string;
  messages: Message[];
  approvals: Approval[];
  input: string;
  busy: boolean;
  loading: boolean;
  live: Live | null;
  elapsed: number;
  error: string | null;
  setInput: (text: string) => void;
  newThread: () => void;
  send: (text?: string) => Promise<void>;
  decide: (approve: boolean) => Promise<void>;
  mark: (index: number, verdict: "good" | "bad", note: string) => Promise<void>;
};

const ChatContext = createContext<Session | null>(null);

// 挂在根 layout、位于会重新挂载的 template 外面：导航只换页面，流式连接和会话继续保留。
export function ChatSessionProvider({ children }: { children: React.ReactNode }) {
  const [thread, setThread] = useState("main");
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<Live | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const running = useRef(false); // 同一轮渲染内的连续点击也不能重复提交
  const revision = useRef(0);

  useEffect(() => {
    let active = true;
    const request = ++revision.current;
    let id = "main";
    try { id = localStorage.getItem("polaris-thread") ?? "main"; } catch { /* 浏览器禁用存储 */ }
    api<{ messages: Message[]; approvals: Approval[] }>(`chat/${id}/messages`)
      .then((r) => {
        if (active && revision.current === request) {
          setMessages(r.messages);
          setApprovals(r.approvals);
        }
      })
      .catch((e) => { if (active && revision.current === request) setError(e.message); })
      .finally(() => {
        if (active && revision.current === request) {
          setThread(id);
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, []);

  function newThread() {
    if (running.current || loading) return;
    ++revision.current;
    const id = `t-${crypto.randomUUID()}`;
    try { localStorage.setItem("polaris-thread", id); } catch { /* 浏览器禁用存储 */ }
    setThread(id);
    setMessages([]);
    setApprovals([]);
    setInput("");
    setError(null);
    setElapsed(0);
  }

  async function mark(index: number, verdict: "good" | "bad", note: string) {
    const target = messages[index];
    if (!target) return;
    const request = revision.current;
    const question = [...messages.slice(0, index)].reverse().find((m) => m.role === "user")?.content ?? "";
    try {
      await api("feedback", { body: { thread_id: thread, question, answer: target.content, verdict, note } });
      setMessages((ms) => ms.map((m) => (m === target ? { ...m, verdict } : m)));
    } catch (e) {
      if (revision.current === request) setError((e as Error).message);
    }
  }

  function take(turn: Turn) {
    if (turn.error) setError(turn.error);
    setApprovals(turn.approvals);
    if (turn.answer || turn.memory_events.length || turn.tool_calls.length) {
      setMessages((ms) => [...ms, { role: "assistant", content: turn.answer, turn }]);
    }
  }

  async function run(path: string, body: unknown) {
    running.current = true;
    ++revision.current;
    setBusy(true);
    setError(null);
    setElapsed(0);
    setLive({ memory: null, steps: [], text: "" });
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    let finished = false;
    try {
      await stream(path, body, (e) => {
        if (finished) return;
        if (e.type === "memory") setLive((l) => l && { ...l, memory: e.events as MemoryEvent[] });
        else if (e.type === "tool") setLive((l) => l && {
          ...l, text: "", steps: [...l.steps.map((s) => ({ ...s, done: true })), { tool: e.tool as string, done: false }],
        });
        else if (e.type === "token") setLive((l) => l && {
          ...l, text: l.text + (e.text as string), steps: l.steps.map((s) => ({ ...s, done: true })),
        });
        else if (e.type === "done") { finished = true; take(e.turn as Turn); }
        else if (e.type === "error") { finished = true; setError(e.message as string); }
      });
      if (!finished) setError("连接提前结束，没有收到完整结果。请检查后端状态。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      clearInterval(timer);
      setLive(null);
      setBusy(false);
      running.current = false;
    }
  }

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || loading || running.current || approvals.length > 0) return;
    setInput("");
    setMessages((ms) => [...ms, { role: "user", content: message }]);
    await run("chat/stream", { message, thread_id: thread });
  }

  async function decide(approve: boolean) {
    if (loading || running.current || approvals.length === 0) return;
    await run(`chat/${thread}/resume/stream`, { approve, reason: approve ? "" : "我没有批准" });
  }

  return (
    <ChatContext.Provider value={{ thread, messages, approvals, input, busy, loading, live, elapsed, error,
                                  setInput, newThread, send, decide, mark }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChatSession() {
  const session = useContext(ChatContext);
  if (!session) throw new Error("ChatSessionProvider is missing");
  return session;
}
