"use client";

import { useState } from "react";
import { ErrorNote } from "@/components/markdown";
import { tool } from "@/lib/client";

import { ResultCards } from "@/components/result-cards";
import { type Result } from "@/lib/results";

export default function Jobs() {
  const [form, setForm] = useState({ query: "", company: "", city: "", job_type: "" });
  const [rows, setRows] = useState<Result[] | null>(null);
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

  const input = "rounded border border-line bg-card px-2 py-1 text-sm";
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">岗位</h1>
      <p className="text-sm text-muted">多来源合并后的大厂岗位。写了“想找什么”就按相关度排（关键词 + 向量 + 图谱 + 重排），不写就按发布时间。</p>
      <div className="flex flex-wrap gap-2">
        <input className={`${input} flex-1 min-w-48`} placeholder="想找什么，如：要求 RAG 的 Agent 后端实习" value={form.query}
               onChange={(e) => setForm({ ...form, query: e.target.value })} onKeyDown={(e) => e.key === "Enter" && search()} />
        <input className={`${input} w-28`} placeholder="公司" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} />
        <input className={`${input} w-24`} placeholder="城市" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
        <select className={input} value={form.job_type} onChange={(e) => setForm({ ...form, job_type: e.target.value })}>
          <option value="">类型不限</option><option value="intern">实习</option><option value="campus">校招</option><option value="social">社招</option>
        </select>
        <button className="rounded bg-accent text-white px-4 text-sm disabled:opacity-50" onClick={search} disabled={busy}>{busy ? "查询中…" : "查"}</button>
      </div>
      <ErrorNote error={error} />
      {rows?.length === 0 && <p className="text-sm text-muted">没有符合条件的岗位。</p>}
      {rows && <ResultCards results={rows} />}
    </div>
  );
}
