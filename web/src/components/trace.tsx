"use client";

// 一次回答的追踪：每次模型调用和工具调用何时开始、用了多久、多少 token、有没有出错。数据来自后端 trace_of（不含内容）。
import { motion } from "motion/react";
import { TOOL_NAMES } from "@/components/chat-session";

export type ModelCall = {
  at_ms: number; ms: number; kind: "tools" | "answer" | "summary" | "error"; tool_calls?: number; input_tokens?: number | null;
  output_tokens?: number | null; reasoning_tokens?: number | null; cached_tokens?: number | null; cost_usd?: number | null;
  error?: string; model?: string;
};
export type ToolTrace = { tool: string; at_ms: number; ms: number; ok: boolean; error_kind?: string | null; live?: boolean };
export type Trace = {
  model_calls: ModelCall[]; tools: ToolTrace[];
  totals: { model_calls: number; model_ms: number; input_tokens: number; cached_tokens: number; output_tokens: number;
            reasoning_tokens: number; cost_usd: number | null; tool_calls: number; tool_ms: number; tool_errors: number };
};

const KIND = { tools: "模型 · 决定查什么", answer: "模型 · 写回答", summary: "模型 · 压缩上下文", error: "模型 · 出错" } as const;
const ERRORS: Record<string, string> = {
  invalid_args: "参数不对", tool_error: "工具报错", internal: "内部错误", injected: "注入故障", live_cap: "现查次数到上限",
  per_answer_cap: "调用次数到上限", replay_miss: "回放缺失",
};

export function k(n: number | null | undefined): string {
  if (n == null) return "—";
  return n >= 10000 ? `${(n / 1000).toFixed(0)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function TraceTimeline({ trace, seconds }: { trace: Trace; seconds: number }) {
  const rows = [
    ...trace.model_calls.map((m) => ({ label: KIND[m.kind] ?? "模型", at: m.at_ms, ms: m.ms, bad: m.kind === "error", model: true,
                                        note: m.kind === "error" ? m.error : `${k(m.input_tokens)} 进 / ${k(m.output_tokens)} 出` })),
    ...trace.tools.map((t) => ({ label: TOOL_NAMES[t.tool] ?? t.tool, at: t.at_ms, ms: t.ms, bad: !t.ok, model: false,
                                 note: t.ok ? (t.live ? "现查" : "") : ERRORS[t.error_kind ?? ""] ?? "失败" })),
  ].sort((a, b) => a.at - b.at);
  const end = Math.max(seconds * 1000, ...rows.map((r) => r.at + r.ms), 1);
  const t = trace.totals;
  // 老记录可能把未知定价汇总为0，逐次数据不完整时仍不能当作免费。
  const mainCost = trace.model_calls.length === t.model_calls && trace.model_calls.every((m) => m.cost_usd != null)
    ? t.cost_usd : null;
  const cached = t.input_tokens ? Math.round((t.cached_tokens / t.input_tokens) * 100) : 0;
  return (
    <div className="space-y-2" data-testid="trace">
      <div className="space-y-1">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[7.5rem_1fr_6.5rem] items-center gap-2 text-[11.5px]">
            <span className={`truncate ${r.bad ? "text-bad" : r.model ? "text-foreground/80" : "text-muted"}`} title={r.label}>{r.label}</span>
            <div className="relative h-3 rounded bg-soft">
              <motion.div className={`absolute top-0 h-3 rounded ${r.bad ? "bg-bad/70" : r.model ? "bg-accent/70" : "bg-faint/70"}`}
                          style={{ left: `${(r.at / end) * 100}%` }} initial={{ width: 0 }}
                          animate={{ width: `${Math.max(0.8, (r.ms / end) * 100)}%` }} transition={{ duration: 0.4, delay: i * 0.02 }} />
            </div>
            <span className="text-faint tabular-nums truncate" title={r.note}>{(r.ms / 1000).toFixed(1)}s{r.note ? ` · ${r.note}` : ""}</span>
          </div>
        ))}
      </div>
      <p className="text-[11.5px] text-muted">
        模型 {t.model_calls} 次、{(t.model_ms / 1000).toFixed(1)} 秒 · 输入 {k(t.input_tokens)} token（缓存命中 {cached}%）· 输出 {k(t.output_tokens)}
        {t.reasoning_tokens ? `（其中思考 ${k(t.reasoning_tokens)}）` : ""} · 工具 {t.tool_calls} 次、{(t.tool_ms / 1000).toFixed(1)} 秒
        {t.tool_errors ? `，失败 ${t.tool_errors} 次` : ""}
        {mainCost == null ? " · 主模型费用未完整计量" : ` · 主模型估算 $${mainCost.toFixed(4)}`}
        {"（未含记忆与检索费用）"}
      </p>
    </div>
  );
}
