"use client";

import { useState } from "react";
import { Briefcase, Search } from "lucide-react";
import { Button, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { ResultCards } from "@/components/result-cards";
import { tool } from "@/lib/client";
import { type Result } from "@/lib/results";

export default function Jobs() {
  const [form, setForm] = useState({ query: "", company: "", city: "", job_type: "" });
  const [rows, setRows] = useState<Result[] | null>(null);
  const [order, setOrder] = useState<"relevance" | "match">("relevance");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    setBusy(true); setError(null);
    const args: Record<string, unknown> = { limit: 30 };
    for (const [k, v] of Object.entries(form)) if (v.trim()) args[k] = v.trim();
    try {
      const r = await tool<{ items: Result[] }>("jobs_search", args);
      setRows(r.items.map((row) => ({ ...row, kind: "job" })));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const hasMatch = rows?.some((r) => r.match);
  const shown = rows && order === "match" ? [...rows].sort((a, b) => (b.match?.score ?? -1) - (a.match?.score ?? -1)) : rows;
  const field = "rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-faint transition-colors";
  return (
    <div className="space-y-6">
      <PageHeader title="岗位">
        多个来源合并后的大厂岗位。写了想找什么就按相关度排（关键词、向量、图谱、重排），不写就按发布时间；卡片上是和你条件的逐条对比。
      </PageHeader>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void search(); }}>
        <div className="relative flex-1 min-w-56">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <input aria-label="想找什么" className={`${field} w-full pl-9`} placeholder="想找什么，比如：要求 RAG 的 Agent 后端实习" value={form.query}
                 onChange={(e) => setForm({ ...form, query: e.target.value })} />
        </div>
        <input aria-label="公司" className={`${field} w-28`} placeholder="公司" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} />
        <input aria-label="城市" className={`${field} w-24`} placeholder="城市" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
        <select aria-label="岗位类型" className={field} value={form.job_type} onChange={(e) => setForm({ ...form, job_type: e.target.value })}>
          <option value="">类型不限</option><option value="intern">实习</option><option value="campus">校招</option><option value="social">社招</option>
        </select>
        <Button type="submit" variant="primary" disabled={busy}>{busy ? "查询中…" : "查"}</Button>
      </form>
      <ErrorNote error={error} />
      {rows && rows.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted">
          <span>找到 {rows.length} 个</span>
          {hasMatch && <div role="radiogroup" aria-label="排序" className="flex rounded-lg border border-line p-0.5 text-xs">
            {(["relevance", "match"] as const).map((o) => <button key={o} type="button" role="radio" aria-checked={order === o} onClick={() => setOrder(o)}
              className={`rounded-md px-2.5 py-1 ${order === o ? "bg-soft text-foreground" : "hover:text-foreground"}`}>{o === "relevance" ? "按相关度" : "按和我的匹配"}</button>)}
          </div>}
        </div>
      )}
      {busy && !rows && <div className="grid sm:grid-cols-2 gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40 !rounded-xl" />)}</div>}
      {rows === null && !busy && (
        <div className="rounded-xl border border-line bg-card">
          <EmptyState icon={Briefcase} title="写下想找什么，或者直接点“查”">不写条件会按发布时间列出最新的岗位。</EmptyState>
        </div>
      )}
      {rows?.length === 0 && <p className="text-sm text-muted">没有符合条件的岗位。去掉一两个条件、或换个说法再试试。</p>}
      {shown && <div className={busy ? "opacity-50 transition-opacity" : "transition-opacity"}><ResultCards results={shown} /></div>}
    </div>
  );
}
