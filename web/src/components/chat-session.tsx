"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/client";
import { type Result } from "@/lib/results";

export type Approval = { name: string; args: Record<string, unknown>; description: string };
export type MemoryEvent = {
  op: string; attribute: string; value: string | null; reason: string; label?: string; old_value?: string | null;
  scope_label?: string; qualifier?: string | null;
};
export type ToolStep = {
  tool: string; args: Record<string, unknown>; ok: boolean; returned?: number | null; sources?: string[]; output?: string;
  desc?: string; summary?: string; error?: string;
};
export type Compacted = { summary: string; messages: number; before_tokens: number; after_tokens: number };
export type Turn = {
  answer: string; memory_events: MemoryEvent[]; memory_questions: string[]; approvals: Approval[];
  tool_calls: { tool: string; args: Record<string, unknown>; ok: boolean }[];
  latency_ms: number; error: string | null; question?: string;
  results?: Result[];
  activities?: ToolStep[];
  context?: { tokens: number; limit: number };
  compacted?: Compacted;
};
export type Message = {
  id?: number; role: "user" | "assistant"; content: string; turn?: Turn; verdict?: "good" | "bad"; task_id?: string;
};
export type Activity = {
  type: "tool" | "memory" | "tool_done" | "compact"; tool?: string; args?: Record<string, unknown>; events?: MemoryEvent[];
  desc?: string; ok?: boolean; returned?: number | null; sources?: string[]; summary?: string;
} & Partial<Compacted>;
export type Task = {
  id: string; state: string; kind: string; question?: string; activities: Activity[]; result: Turn | null; error: string | null;
  created_at: string; started_at: string | null;
  live?: { draft?: string; status?: string | null; seq?: number }; cancel_requested?: boolean;
};
export type Step = { tool: string; desc: string; done: boolean; ok?: boolean; summary?: string; args?: Record<string, unknown> };
type Thread = { id: string; title: string; active: boolean; waiting_approval: boolean };
type Snapshot = { messages: Message[]; approvals: Approval[]; tasks: Task[] };
type Outbox = { request_id: string; thread_id: string; message?: string; approve?: boolean; reason?: string };
export type Live = {
  taskId: string; status: string; memory: MemoryEvent[] | null; steps: Step[]; draft: string;
  compacting: boolean; stopping: boolean; queued: number;
};
type Session = {
  thread: string; threads: Thread[]; messages: Message[]; approvals: Approval[]; tasks: Task[];
  input: string; busy: boolean; submitting: boolean; loading: boolean; live: Live | null; elapsed: number; error: string | null;
  setInput: (text: string) => void; newThread: () => void; switchThread: (id: string) => void;
  send: (text?: string) => Promise<void>; decide: (approve: boolean) => Promise<void>;
  mark: (index: number, verdict: "good" | "bad", note: string) => Promise<void>;
  stop: (taskId?: string) => Promise<void>;
};
export const TOOL_NAMES: Record<string, string> = {
  jobs_search: "搜岗位", jobs_get: "看岗位详情", posts_search: "搜帖子", posts_get: "看帖子",
  questions_search: "搜面试题", kg_stats: "查知识图谱", memory_card: "看你的记忆",
  memory_history: "查记忆的历史", memory_known_at: "查当时记着什么", memory_forget: "忘掉一条记忆",
};

// 发起的工具和返回的结果按顺序配对（同名工具并行时按先后）
export function stepsOf(activities: Activity[]): Step[] {
  const steps: Step[] = [];
  for (const a of activities) {
    if (a.type === "tool" && a.tool) steps.push({ tool: a.tool, desc: a.desc ?? TOOL_NAMES[a.tool] ?? "处理资料", done: false, args: a.args });
    else if (a.type === "tool_done" && a.tool) {
      const step = steps.find((s) => s.tool === a.tool && !s.done);
      if (step) Object.assign(step, { done: true, ok: a.ok, summary: a.summary });
    }
  }
  return steps;
}

function liveOf(running: Task, queued: number): Live {
  const steps = stepsOf(running.activities);
  const memory = running.activities.find((a) => a.type === "memory")?.events ?? null;
  const draft = running.live?.draft ?? "";
  const compacting = running.live?.status === "compacting";
  const pending = steps.find((s) => !s.done);
  let status = running.state === "queued" ? "消息已收到，正在排队"
    : running.cancel_requested ? "正在停止"
    : compacting ? "正在把前面的对话压缩成摘要"
    : draft ? "正在写回答"
    : pending ? `正在${pending.desc}`
    : steps.length ? "正在看结果、组织回答"
    : "正在理解你的问题";
  if (queued && running.state === "running") status += ` · 还有 ${queued} 条消息在排队`;
  return { taskId: running.id, status, memory, steps, draft, compacting, stopping: !!running.cancel_requested, queued };
}
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

  // 正在跑的那条单独快速刷新（回答草稿、步骤），整段对话仍按原来的节奏刷新
  const runningId = tasks.find((t) => t.state === "running")?.id;
  useEffect(() => {
    if (!runningId) return;
    const version = generation.current;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const out = await api<Task>(`chat/tasks/${encodeURIComponent(runningId)}`);
        if (!active || version !== generation.current || !out?.id || !Array.isArray(out.activities)) return;
        setTasks((old) => old.map((t) => t.id !== out.id ? t
          : (out.live?.seq ?? 0) >= (t.live?.seq ?? 0) || out.activities.length > t.activities.length ? { ...t, ...out } : t));
        if (!ACTIVE.has(out.state)) void refresh.current();
      } catch { /* 下一次再取；整段刷新会报连接问题 */ }
    }, 400);
    return () => { active = false; clearInterval(timer); };
  }, [runningId]);

  async function stop(taskId?: string) {
    const target = taskId ?? (tasks.find((t) => t.state === "running") ?? tasks.find((t) => t.state === "queued"))?.id;
    if (!target) return;
    try {
      const out = await api<Task>(`chat/tasks/${encodeURIComponent(target)}/cancel`, { method: "POST", body: {} });
      if (out?.id) setTasks((old) => old.map((t) => (t.id === out.id ? { ...t, ...out } : t)));
    } catch (e) { setError((e as Error).message); }
    void refresh.current();
  }

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
  const queued = tasks.filter((t) => t.state === "queued").length;
  const live: Live | null = running ? liveOf(running, queued) : null;
  return <ChatContext.Provider value={{ thread, threads, messages, approvals, tasks, input, busy, submitting, loading,
    live, elapsed, error, setInput, newThread, switchThread, send, decide, mark, stop }}>{children}</ChatContext.Provider>;
}

export function useChatSession() {
  const session = useContext(ChatContext);
  if (!session) throw new Error("ChatSessionProvider is missing");
  return session;
}
