"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/client";

export type Approval = { name: string; args: Record<string, unknown>; description: string };
export type MemoryEvent = { op: string; attribute: string; value: string | null; reason: string };
export type Turn = {
  answer: string; memory_events: MemoryEvent[]; memory_questions: string[]; approvals: Approval[];
  tool_calls: { tool: string; args: Record<string, unknown>; ok: boolean }[];
  latency_ms: number; error: string | null; question?: string;
};
export type Message = { id?: number; role: "user" | "assistant"; content: string; turn?: Turn; verdict?: "good" | "bad" };
export type Activity = { type: "tool" | "memory"; tool?: string; args?: Record<string, unknown>; events?: MemoryEvent[] };
export type Task = {
  id: string; state: string; kind: string; question?: string; activities: Activity[]; result: Turn | null; error: string | null;
  created_at: string; started_at: string | null;
};
type Thread = { id: string; title: string; active: boolean; waiting_approval: boolean };
type Snapshot = { messages: Message[]; approvals: Approval[]; tasks: Task[] };
type Outbox = { request_id: string; thread_id: string; message?: string; approve?: boolean; reason?: string };
type Live = { status: string; memory: MemoryEvent[] | null; steps: { tool: string; done: boolean }[]; text: string };
type Session = {
  thread: string; threads: Thread[]; messages: Message[]; approvals: Approval[]; tasks: Task[];
  input: string; busy: boolean; submitting: boolean; loading: boolean; live: Live | null; elapsed: number; error: string | null;
  setInput: (text: string) => void; newThread: () => void; switchThread: (id: string) => void;
  send: (text?: string) => Promise<void>; decide: (approve: boolean) => Promise<void>;
  mark: (index: number, verdict: "good" | "bad", note: string) => Promise<void>;
};
const ChatContext = createContext<Session | null>(null);
const ACTIVE = new Set(["queued", "running"]);
function stored(key: string) { try { return localStorage.getItem(key); } catch { return null; } }
function store(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* 禁用存储 */ }
}
function pending(id: string): Outbox | null {
  try { return JSON.parse(stored(`polaris-outbox-${id}`) ?? "null"); } catch { return null; }
}
async function post(out: Outbox) {
  return api<Task>(out.message !== undefined ? "chat/tasks" : `chat/${encodeURIComponent(out.thread_id)}/resume/tasks`, { body: out });
}

// 根 layout 保留草稿；数据库拥有任务、进度和正式记录。刷新/关闭网页后重新查询即可恢复。
export function ChatSessionProvider({ children }: { children: React.ReactNode }) {
  const [thread, setThread] = useState("main");
  const [ready, setReady] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [input, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clock, setClock] = useState(0);
  const sending = useRef(false);
  const actionError = useRef<string | null>(null);
  const operation = useRef(0);
  const refresh = useRef<() => Promise<void>>(async () => {});
  const generation = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const id = stored("polaris-thread") ?? "main";
      setThread(id); setDraft(stored(`polaris-draft-${id}`) ?? ""); setReady(true);
    }, 0);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (!ready) return;
    const version = ++generation.current;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let fetching = false;
    let rerun = false;
    actionError.current = null;
    const current = () => active && version === generation.current;
    async function load() {
      if (!current()) return;
      if (fetching || sending.current) { rerun = true; return; }
      fetching = true;
      const startedOperation = operation.current;
      let wait = 5000;
      try {
        const out = pending(thread);
        if (out) {
          setSubmitting(true);
          try { await post(out); store(`polaris-outbox-${thread}`, null); }
          catch (e) {
            if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
              store(`polaris-outbox-${thread}`, null);
              if (out.message && current()) { setDraft(out.message); store(`polaris-draft-${thread}`, out.message); }
            }
            throw e;
          }
          finally { if (current()) setSubmitting(false); }
        }
        const result = await api<Snapshot>(`chat/${encodeURIComponent(thread)}/messages`);
        const recent = await api<Thread[]>("chat/threads");
        if (!current() || startedOperation !== operation.current) { rerun = true; return; }
        setMessages((old) => result.messages.map((m) => ({ ...m, verdict: old.find((p) => p.id === m.id)?.verdict })));
        setApprovals(result.approvals); setTasks(result.tasks ?? []); setThreads(recent);
        setError(actionError.current);
        setClock(Date.now());
        if (result.tasks?.some((t) => ACTIVE.has(t.state))) wait = 1000;
      } catch (e) {
        if (current()) setError(`${(e as Error).message}。正在重连，后端已接收的任务会继续。`);
        wait = 1000;
      } finally {
        fetching = false;
        if (current()) { setLoading(false); clearTimeout(timer); timer = setTimeout(load, rerun ? 0 : wait); rerun = false; }
      }
    }
    refresh.current = load;
    const focus = () => { clearTimeout(timer); void load(); };
    window.addEventListener("focus", focus);
    void load();
    return () => { active = false; clearTimeout(timer); window.removeEventListener("focus", focus); };
  }, [thread, ready]);

  function setInput(text: string) { setDraft(text); store(`polaris-draft-${thread}`, text); }
  function switchThread(id: string) {
    if (sending.current || submitting || loading || id === thread) return;
    ++generation.current;
    store("polaris-thread", id); setLoading(true); setError(null); setMessages([]); setApprovals([]); setTasks([]);
    setDraft(stored(`polaris-draft-${id}`) ?? ""); setThread(id);
  }
  function newThread() { switchThread(`t-${crypto.randomUUID()}`); }

  async function submit(out: Outbox) {
    if (sending.current || submitting || loading) return;
    sending.current = true; ++operation.current; actionError.current = null; setSubmitting(true); setError(null);
    store(`polaris-outbox-${thread}`, JSON.stringify(out));
    try { await post(out); store(`polaris-outbox-${thread}`, null); }
    catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        store(`polaris-outbox-${thread}`, null);
        if (out.message) setInput(out.message);
        actionError.current = (e as Error).message;
      }
      setError(`${(e as Error).message}。正在确认发送状态，请勿重复发送。`);
    } finally {
      sending.current = false; setSubmitting(false); await refresh.current();
    }
  }
  async function send(text?: string) {
    const message = text ?? input;
    if (!message.trim() || loading || sending.current || submitting || pending(thread) || approvals.length) return;
    setInput("");
    await submit({ request_id: crypto.randomUUID(), thread_id: thread, message });
  }
  async function decide(approve: boolean) {
    if (!approvals.length || pending(thread)) return;
    await submit({ request_id: crypto.randomUUID(), thread_id: thread, approve, reason: approve ? "" : "我没有批准" });
  }
  async function mark(index: number, verdict: "good" | "bad", note: string) {
    const target = messages[index];
    if (!target) return;
    const version = generation.current;
    const question = target.turn?.question ?? [...messages.slice(0, index)].reverse().find((m) => m.role === "user")?.content ?? "";
    try {
      await api("feedback", { body: { thread_id: thread, question, answer: target.content, verdict, note } });
      if (generation.current === version) setMessages((ms) => ms.map((m) => m.id === target.id ? { ...m, verdict } : m));
    } catch (e) { if (generation.current === version) setError((e as Error).message); }
  }

  const running = tasks.find((t) => t.state === "running") ?? tasks.find((t) => t.state === "queued");
  const busy = !!running;
  const elapsed = running ? Math.max(0, Math.round((clock - Date.parse(running.started_at ?? running.created_at)) / 1000)) : 0;
  const lastTool = running?.activities.filter((a) => a.type === "tool").at(-1);
  const queued = tasks.filter((t) => t.state === "queued").length;
  const live: Live | null = running ? {
    status: running.state === "queued" ? "消息已收到，正在排队" : lastTool ? "正在查找与你的问题相关的资料" : "正在处理你的请求",
    memory: running.activities.find((a) => a.type === "memory")?.events ?? null,
    steps: running.activities.filter((a) => a.type === "tool").map((a) => ({ tool: a.tool ?? "", done: false })), text: "",
  } : null;
  if (live && queued && running?.state === "running") live.status += ` · 还有 ${queued} 条消息在排队`;
  return <ChatContext.Provider value={{ thread, threads, messages, approvals, tasks, input, busy, submitting, loading,
    live, elapsed, error, setInput, newThread, switchThread, send, decide, mark }}>{children}</ChatContext.Provider>;
}

export function useChatSession() {
  const session = useContext(ChatContext);
  if (!session) throw new Error("ChatSessionProvider is missing");
  return session;
}
