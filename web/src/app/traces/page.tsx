"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { Activity } from "lucide-react";
import { EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { useChatSession } from "@/components/chat-session";
import { k } from "@/components/trace";
import { api } from "@/lib/client";
import { hideRefs } from "@/lib/refs";

type ToolRow = { tool: string; calls: number; errors: number; p50_ms: number | null; error_kinds: Record<string, number> };
type Row = {
  id: number; thread_id: string; at: string; question: string; seconds: number; model_calls: number | null; tool_calls: number | null;
  tool_errors: number | null; input_tokens: number | null; output_tokens: number | null; compacted: boolean; live: number;
};
type Overview = {
  days: number; answers: number; latency: { p50: number | null; p90: number | null; max: number | null };
  per_answer: { model_calls: number | null; tool_calls: number | null; input_tokens: number | null; output_tokens: number | null };
  cache_hit: number | null; tools: { calls: number; errors: number; error_kinds: Record<string, number>; by_tool: ToolRow[] };
  tasks: Record<string, number>; compacted: number; live_lookups: number; memory_extract_p50_ms: number | null; recent: Row[];
};
const TOOL_NAMES: Record<string, string> = {
  jobs_search: "搜岗位", jobs_get: "看岗位详情", jobs_trend: "看招聘变化", posts_search: "搜帖子", posts_get: "看帖子",
  questions_search: "搜面试题", kg_stats: "查知识图谱", memory_card: "看记忆", memory_history: "查记忆历史",
  memory_known_at: "查当时记忆", memory_forget: "忘掉记忆", source_open: "打开原链接", platform_search: "现搜",
};
const ERRORS: Record<string, string> = {
  invalid_args: "参数不对", tool_error: "工具报错", internal: "内部错误", injected: "注入故障", live_cap: "现查到上限",
  per_answer_cap: "次数到上限", replay_miss: "回放缺失", unknown: "未分类",
};
const STATES: Record<string, string> = { completed: "完成", failed: "失败", cancelled: "停止", interrupted: "中断", waiting_approval: "等批准",
  continued: "已批准", running: "进行中", queued: "排队" };

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-xl font-semibold tabular-nums mt-0.5">{value}</p>
      {hint && <p className="text-[11px] text-faint mt-0.5">{hint}</p>}
    </div>
  );
}

export default function Traces() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const { switchThread } = useChatSession();
  useEffect(() => {
    let active = true;
    api<Overview>(`traces?days=${days}`).then((d) => { if (active) setData(d); }).catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [days]);

  const errRate = data?.tools?.calls ? Math.round((data.tools.errors / data.tools.calls) * 1000) / 10 : null;
  const maxCalls = Math.max(1, ...(data?.tools?.by_tool?.map((t) => t.calls) ?? [1]));
  return (
    <div className="space-y-6">
      <PageHeader title="追踪" right={
        <div role="radiogroup" aria-label="时间范围" className="flex rounded-lg border border-line p-0.5 text-xs">
          {[1, 7, 30].map((d) => <button key={d} type="button" role="radio" aria-checked={days === d} onClick={() => { setData(null); setDays(d); }}
            className={`rounded-md px-2.5 py-1 ${days === d ? "bg-soft text-foreground" : "text-muted hover:text-foreground"}`}>{d === 1 ? "今天" : `${d} 天`}</button>)}
        </div>
      }>
        每次回答用了多久、调了几次模型和工具、多少 token、工具出了什么错。数据来自运行时记下的表，不额外埋点；单次回答的时间线在对话里点开「思考了 N 秒」看。
      </PageHeader>
      <ErrorNote error={error} />
      {data === null ? <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20 !rounded-xl" />)}</div>
        : !data.answers ? <div className="rounded-xl border border-line bg-card"><EmptyState icon={Activity} title="这段时间还没有回答">去对话里问一句，这里就有数据了。</EmptyState></div>
        : <>
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Tile label="回答" value={String(data.answers)} hint={Object.entries(data.tasks).map(([s, n]) => `${STATES[s] ?? s} ${n}`).join(" · ")} />
            <Tile label="用时 中位 / 九成" value={`${data.latency.p50 ?? "—"}s / ${data.latency.p90 ?? "—"}s`} hint={`最慢 ${data.latency.max ?? "—"}s`} />
            <Tile label="每次回答 模型 / 工具" value={`${data.per_answer.model_calls ?? "—"} / ${data.per_answer.tool_calls ?? "—"} 次`}
                  hint={`记忆抽取中位 ${data.memory_extract_p50_ms ? (data.memory_extract_p50_ms / 1000).toFixed(1) + "s" : "—"}（和回答并行）`} />
            <Tile label="每次回答 token 进 / 出" value={`${k(data.per_answer.input_tokens)} / ${k(data.per_answer.output_tokens)}`}
                  hint={`缓存命中 ${data.cache_hit != null ? Math.round(data.cache_hit * 100) + "%" : "—"}`} />
            <Tile label="工具调用" value={String(data.tools.calls)} hint={`失败 ${data.tools.errors}（${errRate ?? 0}%）`} />
            <Tile label="失败分类" value={Object.keys(data.tools.error_kinds).length ? String(Object.keys(data.tools.error_kinds).length) + " 类" : "无"}
                  hint={Object.entries(data.tools.error_kinds).map(([k2, n]) => `${ERRORS[k2] ?? k2} ${n}`).join(" · ") || "没有失败"} />
            <Tile label="上下文压缩" value={`${data.compacted} 次`} hint="超过 4.8 万 token 才压" />
            <Tile label="现查" value={`${data.live_lookups} 次`} hint="打开原链接、去平台现搜" />
          </motion.div>

          <section className="space-y-2">
            <h2 className="text-sm font-medium text-muted">按工具</h2>
            <div className="rounded-xl border border-line bg-card divide-y divide-line">
              {data.tools.by_tool.map((t, i) => (
                <div key={t.tool} className="grid grid-cols-[7rem_1fr_9rem] sm:grid-cols-[8rem_1fr_5rem_12rem] items-center gap-3 px-4 py-2 text-sm">
                  <span className="truncate">{TOOL_NAMES[t.tool] ?? t.tool}</span>
                  <div className="h-2 rounded bg-soft overflow-hidden">
                    <motion.div className="h-full rounded bg-accent/70" initial={{ width: 0 }} animate={{ width: `${(t.calls / maxCalls) * 100}%` }}
                                transition={{ duration: 0.5, delay: i * 0.03 }} />
                  </div>
                  <span className="text-xs text-muted tabular-nums hidden sm:block">中位 {t.p50_ms != null ? `${t.p50_ms}ms` : "—"}</span>
                  <span className={`text-xs tabular-nums ${t.errors ? "text-bad" : "text-muted"}`}>
                    {t.calls} 次{t.errors ? `，失败 ${t.errors}（${Object.entries(t.error_kinds).map(([k2, n]) => `${ERRORS[k2] ?? k2} ${n}`).join("、")}）` : ""}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-medium text-muted">最近的回答</h2>
            <div className="rounded-xl border border-line bg-card divide-y divide-line overflow-hidden">
              {data.recent.map((r) => (
                <button key={r.id} type="button" onClick={() => { switchThread(r.thread_id); router.push("/chat"); }}
                        className="w-full text-left grid grid-cols-[1fr_auto] gap-3 px-4 py-2.5 hover:bg-soft/60 transition-colors">
                  <span className="min-w-0">
                    <span className="block text-sm truncate">{hideRefs(r.question) || "（批准后的继续）"}</span>
                    <span className="block text-[11px] text-faint" suppressHydrationWarning>
                      {new Date(r.at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                      {r.compacted ? " · 压缩过上下文" : ""}{r.live ? ` · 现查 ${r.live} 次` : ""}{r.tool_errors ? ` · 工具失败 ${r.tool_errors}` : ""}
                    </span>
                  </span>
                  <span className="text-xs text-muted tabular-nums text-right self-center">
                    {r.seconds}s · 模型 {r.model_calls ?? "—"} · 工具 {r.tool_calls ?? "—"}<br />
                    <span className="text-faint">{k(r.input_tokens)} 进 / {k(r.output_tokens)} 出</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        </>}
    </div>
  );
}
