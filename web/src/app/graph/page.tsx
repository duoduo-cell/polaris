"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { BarChart3, Network, Search, Sparkles } from "lucide-react";
import { Button, Card, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { tool } from "@/lib/client";

type Group = { group: string; count: number; examples: string[] };
type Stats = { of: string; matched: number; filters: Record<string, string>; groups: Group[]; hint?: string };

const PRESETS = [
  { label: "哪些公司的岗位要求 LangGraph", args: { of: "jobs", group_by: "company", skill: "LangGraph" } },
  { label: "腾讯哪些事业群在招 Agent", args: { of: "jobs", group_by: "unit", company: "腾讯", direction: "Agent" } },
  { label: "字节的面试题常考哪些技能", args: { of: "questions", group_by: "skill", company: "字节" } },
  { label: "多模态岗位在哪些城市", args: { of: "jobs", group_by: "city", direction: "多模态" } },
  { label: "大厂 Agent 岗位最常要求的技能", args: { of: "jobs", group_by: "skill", direction: "Agent" } },
];
const OF_NAMES: Record<string, string> = { jobs: "岗位", questions: "面试题", posts: "帖子" };

export default function Graph() {
  const [args, setArgs] = useState<Record<string, string>>({ of: "jobs", group_by: "company", skill: "RAG", company: "", direction: "", city: "" });
  const [stats, setStats] = useState<Stats | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(a: Record<string, string>) {
    setBusy(true); setError(null);
    const clean: Record<string, unknown> = { top: 15 };
    for (const [k, v] of Object.entries(a)) if (v) clean[k] = v;
    try {
      setStats(await tool<Stats>("kg_stats", clean));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const field = "rounded-xl border border-line bg-card px-3 py-1.5 text-sm outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/15 transition";
  const max = Math.max(1, ...(stats?.groups.map((g) => g.count) ?? [1]));
  return (
    <div className="space-y-5">
      <PageHeader icon={Network} title="知识图谱">
        公司、部门、岗位、城市、技能、方向、帖子、面试题之间的关系。按条件筛，再按一类分组数一数
      </PageHeader>

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p, i) => (
          <motion.button key={p.label} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
                         whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }}
                         className="inline-flex items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1.5 text-sm hover:border-accent/50 hover:text-accent transition-colors"
                         onClick={() => { const a = { company: "", skill: "", direction: "", city: "", ...p.args }; setArgs(a); run(a); }}>
            <Sparkles size={13} />{p.label}
          </motion.button>
        ))}
      </div>

      <Card className="flex flex-wrap gap-2 items-center">
        <select className={field} value={args.of} onChange={(e) => setArgs({ ...args, of: e.target.value })}>
          <option value="jobs">数岗位</option><option value="questions">数面试题</option><option value="posts">数帖子</option>
        </select>
        <span className="text-sm text-muted">按</span>
        <select className={field} value={args.group_by} onChange={(e) => setArgs({ ...args, group_by: e.target.value })}>
          <option value="company">公司</option><option value="skill">技能</option><option value="unit">部门</option>
          <option value="city">城市</option><option value="direction">方向</option>
        </select>
        <span className="text-sm text-muted">分组，条件</span>
        <input className={`${field} w-24`} placeholder="公司" value={args.company} onChange={(e) => setArgs({ ...args, company: e.target.value })} />
        <input className={`${field} w-28`} placeholder="技能" value={args.skill} onChange={(e) => setArgs({ ...args, skill: e.target.value })} />
        <input className={`${field} w-24`} placeholder="方向" value={args.direction} onChange={(e) => setArgs({ ...args, direction: e.target.value })} />
        <input className={`${field} w-20`} placeholder="城市" value={args.city} onChange={(e) => setArgs({ ...args, city: e.target.value })} />
        <Button icon={busy ? undefined : Search} onClick={() => run(args)} disabled={busy} className="ml-auto">
          {busy ? <span className="dots"><span /><span /><span /></span> : "统计"}
        </Button>
      </Card>
      <ErrorNote error={error} />

      {busy && !stats && <Card className="space-y-3">{[90, 70, 55, 40, 30].map((w) => <Skeleton key={w} className="h-5" style={{ width: `${w}%` }} />)}</Card>}
      {!busy && !stats && (
        <Card><EmptyState icon={BarChart3} title="选一个上面的问题，或者自己组合条件">比如“数岗位，按公司分组，技能 = RAG”：看哪些公司的岗位要求 RAG。</EmptyState></Card>
      )}
      <AnimatePresence mode="wait">
        {stats && (
          <motion.div key={JSON.stringify(stats.filters) + stats.of} initial={{ opacity: 0, y: 8 }} animate={{ opacity: busy ? 0.5 : 1, y: 0 }} exit={{ opacity: 0 }}>
            <Card className="space-y-3">
              <p className="text-sm text-muted">
                符合条件的{OF_NAMES[stats.of] ?? ""}共 <span className="font-semibold text-foreground">{stats.matched}</span> 个
              </p>
              {stats.hint && <p className="text-sm text-muted">{stats.hint}</p>}
              <div className="space-y-2">
                {stats.groups.map((g, i) => (
                  <div key={g.group} className="group grid grid-cols-[8rem_1fr_2.5rem] sm:grid-cols-[10rem_1fr_2.5rem_12rem] items-center gap-3 text-sm">
                    <span className="truncate font-medium" title={g.group}>{g.group}</span>
                    <div className="h-6 rounded-lg bg-soft overflow-hidden">
                      <motion.div className="h-full rounded-lg bg-gradient-to-r from-accent to-accent-2 group-hover:brightness-110"
                                  initial={{ width: 0 }} animate={{ width: `${(g.count / max) * 100}%` }}
                                  transition={{ duration: 0.6, delay: i * 0.04, ease: "easeOut" }} />
                    </div>
                    <span className="text-right tabular-nums">{g.count}</span>
                    <span className="hidden sm:block text-muted truncate text-xs" title={g.examples.join(" ")}>{g.examples.join(" ")}</span>
                  </div>
                ))}
              </div>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
