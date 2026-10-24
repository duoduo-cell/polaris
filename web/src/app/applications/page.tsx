"use client";

// 投递漏斗：每个岗位走到过的最远一步（收藏 → 投递 → 有回复 → 面试 → Offer），各渠道转化、每周投递量、被拒反馈。
// 只统计你在岗位详情里明确记下的进度；北极星不替你投递。
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Send } from "lucide-react";
import { EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { FollowupList } from "@/components/result-cards";
import { api } from "@/lib/client";

type Funnel = {
  steps: { key: string; label: string; count: number }[];
  rates: { reply: number | null; interview: number | null; offer: number | null };
  channels: { channel: string; applied: number; replied: number; interview: number }[];
  weeks: { week: string; applied: number }[];
  rejections: { for: string | null; reason: string }[];
  note: string;
};

const pct = (r: number | null) => (r === null ? "—" : `${Math.round(r * 100)}%`);

export default function Applications() {
  const [data, setData] = useState<Funnel | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; api<Funnel>("funnel").then((d) => { if (active) setData(d); })
    .catch((e) => { if (active) setError((e as Error).message); }); return () => { active = false; }; }, []);
  const top = Math.max(1, ...(data?.steps.map((s) => s.count) ?? [1]));
  const weekTop = Math.max(1, ...(data?.weeks.map((w) => w.applied) ?? [1]));
  return (
    <div className="space-y-8">
      <PageHeader title="投递">
        每个岗位按走到过的最远一步统计。进度在岗位详情里记；北极星只帮你起草跟进消息，不替你投递或发送。
      </PageHeader>
      <ErrorNote error={error} />
      {!data && !error && <Skeleton className="h-48 !rounded-xl" />}
      {data && (data.steps[1].count === 0 && data.steps[0].count === 0 ? (
        <div className="rounded-xl border border-line bg-card">
          <EmptyState icon={Send} title="还没有记下的投递">在对话或岗位页打开一个岗位，收藏或把状态改成“已投递”，这里就会开始统计。</EmptyState>
        </div>
      ) : <>
        <section className="rounded-xl border border-line bg-card p-4 space-y-3" aria-label="投递漏斗">
          {data.steps.map((s, i) => <div key={s.key} className="flex items-center gap-3 text-sm">
            <span className="w-20 shrink-0 text-muted">{s.label}</span>
            <div className="flex-1 h-6 rounded-md bg-soft overflow-hidden">
              <motion.div initial={{ width: 0 }} animate={{ width: `${(s.count / top) * 100}%` }} transition={{ delay: i * 0.05 }}
                          className="h-full rounded-md bg-accent/70" />
            </div>
            <span className="w-8 text-right tabular-nums font-medium">{s.count}</span>
          </div>)}
          <p className="text-xs text-muted pt-1">回复率 {pct(data.rates.reply)} · 面试率 {pct(data.rates.interview)} · Offer 率 {pct(data.rates.offer)}（都以已投递为分母）</p>
        </section>

        <div className="grid md:grid-cols-2 gap-4">
          <section className="rounded-xl border border-line bg-card p-4" aria-label="各渠道">
            <h2 className="font-semibold mb-3">各渠道</h2>
            {data.channels.length ? <table className="w-full text-sm">
              <thead><tr className="text-xs text-muted text-left"><th className="font-normal pb-2">渠道</th><th className="font-normal pb-2 text-right">投递</th>
                <th className="font-normal pb-2 text-right">有回复</th><th className="font-normal pb-2 text-right">面试</th></tr></thead>
              <tbody>{data.channels.map((c) => <tr key={c.channel} className="border-t border-line">
                <td className="py-1.5">{c.channel}</td><td className="text-right tabular-nums">{c.applied}</td>
                <td className="text-right tabular-nums">{c.replied}</td><td className="text-right tabular-nums">{c.interview}</td></tr>)}</tbody>
            </table> : <p className="text-sm text-muted">还没有投递。</p>}
          </section>
          <section className="rounded-xl border border-line bg-card p-4" aria-label="每周投递">
            <h2 className="font-semibold mb-3">最近四周投递</h2>
            <div className="flex items-end gap-3 h-28">
              {data.weeks.map((w) => <div key={w.week} className="flex-1 flex flex-col items-center gap-1">
                <span className="text-xs tabular-nums">{w.applied}</span>
                <div className="w-full rounded-t-md bg-accent/60" style={{ height: `${(w.applied / weekTop) * 80}px`, minHeight: 2 }} />
                <span className="text-[11px] text-muted">{w.week.slice(5)} 起</span>
              </div>)}
            </div>
          </section>
        </div>

        <section className="rounded-xl border border-line bg-card p-4" aria-label="被拒的反馈">
          <h2 className="font-semibold">被拒的反馈</h2>
          <p className="text-xs text-muted mt-0.5 mb-3">岗位标成“未通过”时写下的理由，已记进你的卡片；问差距、找岗位、备面试时北极星会对照这些。</p>
          {data.rejections.length ? <ul className="space-y-2 text-sm">{data.rejections.map((r, i) => <li key={i}>
            <span className="text-muted">{r.for ?? "未说明岗位"}：</span>{r.reason}</li>)}</ul>
            : <p className="text-sm text-muted">还没有记下的被拒反馈。</p>}
        </section>
        <p className="text-xs text-faint">{data.note}</p>
      </>)}
      <FollowupList />
    </div>
  );
}
