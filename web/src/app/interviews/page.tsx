"use client";

import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { BookOpen, ChevronLeft, ChevronRight } from "lucide-react";
import { Button, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { ResultCards } from "@/components/result-cards";
import { api } from "@/lib/client";
import { type Result } from "@/lib/results";

const TABS = { interview: "面经", questions: "面试题", referral: "内推", recruitment: "招聘线索", study: "备考整理", uncertain: "待核实资料" };
type Tab = keyof typeof TABS;
type Page = { items: Result[]; next_offset: number | null };
const EMPTY = { company: "", role: "", job_type: "", source: "", round: "", text: "" };
export default function Interviews() {
  const [tab, setTab] = useState<Tab>("interview");
  const [draft, setDraft] = useState(EMPTY);
  const [filters, setFilters] = useState(EMPTY);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page>({ items: [], next_offset: null });
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({ limit: "20", offset: String(offset) });
    for (const [k, v] of Object.entries(filters)) if (v.trim() && (k !== "round" || tab === "questions")) params.set(k, v.trim());
    const types: Partial<Record<Tab, string>> = { interview: "面经", referral: "内推", recruitment: "招聘" };
    if (types[tab]) params.set("post_type", types[tab]!);
    if (tab === "interview" || tab === "questions") params.set("material", "firsthand");
    if (tab === "study") params.set("material", "study");
    if (tab === "uncertain") params.set("material", "uncertain");
    api<Page>(`knowledge/${tab === "questions" ? "questions" : "posts"}?${params}`).then((r) => { if (active) setPage({ items: r.items ?? [], next_offset: r.next_offset ?? null }); })
      .catch((e) => { if (active) { setError((e as Error).message); setPage({ items: [], next_offset: null }); } })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [tab, filters, offset]);
  function change(next: Tab) {
    if (next === tab) return;
    setBusy(true); setError(null); setPage({ items: [], next_offset: null }); setOffset(0); setTab(next);
  }
  const field = "rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-faint transition-colors";
  return <div className="space-y-6">
    <PageHeader title="面经与内推">
      面经、备考、内推分开看。面试公司按正文归属，评论里提到的公司单独说明；帖子发布时间和实际面试日期分开。
    </PageHeader>
    <div role="tablist" aria-label="资料类型" className="flex flex-wrap gap-1 border-b border-line">
      {Object.entries(TABS).map(([key, label]) => <button role="tab" aria-selected={tab === key} key={key} onClick={() => change(key as Tab)}
        className={`relative px-3 py-2 text-sm transition-colors ${tab === key ? "text-foreground font-medium" : "text-muted hover:text-foreground"}`}>
        {label}
        {tab === key && <motion.span layoutId="tab-underline" className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-foreground" />}
      </button>)}
    </div>
    <form className="flex flex-wrap gap-2" onSubmit={(e) => {
      e.preventDefault(); setBusy(true); setError(null); setPage({ items: [], next_offset: null }); setOffset(0); setFilters({ ...draft });
    }}>
      <input aria-label="公司" className={`${field} w-32`} placeholder="公司，如：字节" value={draft.company} onChange={(e) => setDraft({ ...draft, company: e.target.value })} />
      <input aria-label="岗位方向" className={`${field} w-36`} placeholder="岗位方向，如：算法" value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })} />
      <input aria-label="资料关键词" className={`${field} flex-1 min-w-40`} placeholder="原文 / 题目关键词" value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
      <select aria-label="岗位类型" className={field} value={draft.job_type} onChange={(e) => setDraft({ ...draft, job_type: e.target.value })}>
        <option value="">岗位类型不限</option><option value="intern">实习</option><option value="campus">校招</option><option value="social">社招</option>
      </select>
      <select aria-label="资料来源" className={field} value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })}>
        <option value="">来源不限</option><option value="xhs">小红书</option><option value="maimai">脉脉</option><option value="nowcoder">牛客</option>
      </select>
      {tab === "questions" && <select aria-label="面试轮次" className={field} value={draft.round} onChange={(e) => setDraft({ ...draft, round: e.target.value })}>
        <option value="">轮次不限</option>{[1, 2, 3, 4].map((n) => <option key={n} value={`${n}面`}>{n} 面</option>)}
      </select>}
      <Button type="submit" variant="primary" disabled={busy}>筛选</Button>
    </form>
    <ErrorNote error={error} />
    {busy ? <div role="status" aria-label="正在读取资料" className="grid sm:grid-cols-2 gap-3">
      {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32 !rounded-xl" />)}
    </div> : <>
      {!page.items.length && <div className="rounded-xl border border-line bg-card">
        <EmptyState icon={BookOpen} title="没有符合条件的资料">少几个筛选条件再试试。</EmptyState>
      </div>}
      <ResultCards results={page.items} />
      {(offset > 0 || page.next_offset != null) && <div className="flex items-center justify-center gap-3 text-sm">
        <Button variant="ghost" icon={ChevronLeft} disabled={offset === 0} onClick={() => { setBusy(true); setOffset(Math.max(0, offset - 20)); }}>上一页</Button>
        <span className="text-muted tabular-nums">第 {offset / 20 + 1} 页</span>
        <Button variant="ghost" disabled={page.next_offset == null} onClick={() => { setBusy(true); setOffset(page.next_offset!); }}>下一页<ChevronRight size={15} aria-hidden /></Button>
      </div>}
    </>}
  </div>;
}
