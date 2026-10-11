"use client";

import { Check, CircleAlert, Wrench } from "lucide-react";
import { type Activity, type Turn } from "@/components/chat-session";

export const TOOL_NAMES: Record<string, string> = {
  jobs_search: "搜索岗位", jobs_get: "查看岗位详情", posts_search: "搜索帖子", posts_get: "查看帖子",
  questions_search: "搜索面试题", kg_stats: "查询知识图谱", memory_card: "读取记忆",
  memory_history: "查询记忆历史", memory_known_at: "查询当时的记忆", memory_forget: "忘掉记忆",
};
const OPS: Record<string, string> = {
  add: "记下", update: "更新", schedule: "安排", invalidate: "作废", pending: "待确认",
  ask: "需要确认", confirm: "确认", reject: "拒绝", forget: "忘掉",
};
export function ChatActivity({ turn, activities = [] }: { turn?: Turn; activities?: Activity[] }) {
  const tools: NonNullable<Turn["activities"]> = turn?.activities ?? turn?.tool_calls ?? activities.filter((a) => a.type === "tool").map((a) => ({
    tool: a.tool ?? "", args: a.args ?? {}, ok: true,
  }));
  const memory = turn?.memory_events ?? activities.flatMap((a) => a.events ?? []);
  if (!tools.length && !memory.length) return null;
  return (
    <details className="text-xs text-muted">
      <summary className="cursor-pointer select-none inline-flex items-center gap-1.5 hover:text-foreground">
        <Wrench size={12} /> 查看活动{turn ? ` · ${(turn.latency_ms / 1000).toFixed(1)} 秒` : ""}
      </summary>
      <ul className="mt-2 space-y-1 pl-1">
        {memory.filter((e) => OPS[e.op]).map((e, i) => <li key={`m-${i}`}>{OPS[e.op]}：{e.label ? `${e.label} · ` : ""}
          {e.old_value && e.op === "update" ? `${e.old_value} → ` : ""}{e.value ?? e.attribute}{e.scope_label ? `（${e.scope_label}）` : ""}</li>)}
        {tools.map((c, i) => (
          <li key={`t-${i}`} className="flex gap-2 items-start">
            {c.ok ? <Check size={12} className="text-emerald-500 mt-0.5 shrink-0" /> : <CircleAlert size={12} className="text-red-500 mt-0.5 shrink-0" />}
            <span className="shrink-0">{TOOL_NAMES[c.tool] ?? "处理资料"}</span>
            <div className="min-w-0">
              {c.returned != null && <span>找到 {c.returned} 条 · </span>}
              {!!c.sources?.length && <span>{c.sources.join("、")}</span>}
              {!c.ok && <span>调用失败，请重试</span>}
              <details><summary className="cursor-pointer">查看筛选条件</summary>
                <dl>{Object.entries(c.args).filter(([, v]) => v != null).map(([k, v]) => <div key={k}>
                  <dt className="inline">{{ company: "公司", query: "检索内容", role: "岗位", job_type: "岗位类型", material: "资料性质", source: "来源", limit: "条数", round: "轮次", post_type: "资料类型" }[k] ?? k}：</dt>
                  <dd className="inline break-all">{String(v)}</dd>
                </div>)}</dl>
              </details>
            </div>
          </li>
        ))}
      </ul>
    </details>
  );
}
