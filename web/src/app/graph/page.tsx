"use client";

import { useState } from "react";
import { Card, ErrorNote } from "@/components/markdown";
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

  const input = "rounded border border-line bg-card px-2 py-1 text-sm";
  const max = Math.max(1, ...(stats?.groups.map((g) => g.count) ?? [1]));
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">知识图谱</h1>
      <p className="text-sm text-muted">公司、部门、岗位、城市、技能、方向、帖子、面试题之间的关系（Neo4j）。按条件筛，再按一类分组数一数。</p>
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button key={p.label} className="rounded-full border border-line px-3 py-1 text-sm hover:bg-card"
                  onClick={() => { const a = { company: "", skill: "", direction: "", city: "", ...p.args }; setArgs(a); run(a); }}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <select className={input} value={args.of} onChange={(e) => setArgs({ ...args, of: e.target.value })}>
          <option value="jobs">数岗位</option><option value="questions">数面试题</option><option value="posts">数帖子</option>
        </select>
        <span className="text-sm text-muted">按</span>
        <select className={input} value={args.group_by} onChange={(e) => setArgs({ ...args, group_by: e.target.value })}>
          <option value="company">公司</option><option value="skill">技能</option><option value="unit">部门</option>
          <option value="city">城市</option><option value="direction">方向</option>
        </select>
        <span className="text-sm text-muted">分组；条件：</span>
        <input className={`${input} w-24`} placeholder="公司" value={args.company} onChange={(e) => setArgs({ ...args, company: e.target.value })} />
        <input className={`${input} w-28`} placeholder="技能" value={args.skill} onChange={(e) => setArgs({ ...args, skill: e.target.value })} />
        <input className={`${input} w-24`} placeholder="方向" value={args.direction} onChange={(e) => setArgs({ ...args, direction: e.target.value })} />
        <input className={`${input} w-20`} placeholder="城市" value={args.city} onChange={(e) => setArgs({ ...args, city: e.target.value })} />
        <button className="rounded bg-accent text-white px-4 py-1 text-sm disabled:opacity-50" onClick={() => run(args)} disabled={busy}>
          {busy ? "统计中…" : "统计"}
        </button>
      </div>
      <ErrorNote error={error} />
      {stats && (
        <Card className="space-y-2">
          <p className="text-sm text-muted">符合条件的共 {stats.matched} 个</p>
          {stats.hint && <p className="text-sm text-muted">{stats.hint}</p>}
          {stats.groups.map((g) => (
            <div key={g.group} className="flex items-center gap-3 text-sm">
              <span className="w-40 shrink-0 truncate" title={g.group}>{g.group}</span>
              <div className="flex-1 bg-background rounded h-4 overflow-hidden">
                <div className="bg-accent h-4" style={{ width: `${(g.count / max) * 100}%` }} />
              </div>
              <span className="w-8 text-right">{g.count}</span>
              <span className="w-40 text-muted truncate">{g.examples.join(" ")}</span>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
