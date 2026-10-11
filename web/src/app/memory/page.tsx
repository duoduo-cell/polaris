"use client";

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Brain, CircleHelp, History, Quote, Trash2, X } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { api } from "@/lib/client";
import { FollowupList } from "@/components/result-cards";

type Fact = { id: string; attribute: string; value: string; for?: string; scope_label?: string; valid_from?: string; valid_to?: string };
type CardResp = { on: string; facts: Fact[]; waiting_for_confirmation?: { id: string; statement: string }[] };
type Version = Fact & { status: string; kind: string; recorded: string; superseded?: string; from_text: string };

// 卡片上显示的属性名 → 后端属性名（删和查历史要用）
const ATTRS: Record<string, string> = {
  想找的方向: "target_role", 想去的城市: "target_city", 找什么类型: "job_type", 最早什么时候能开始: "available_from",
  每周能到岗几天: "days_per_week", "能实习多久（月）": "duration_months", 毕业年份: "grad_year", 学校: "school", 学历: "degree",
  专业: "major", 会的技术: "skill", 做过的项目: "project", 想去的公司: "company_like", 不想去的公司或团队: "company_avoid",
  投递过的岗位: "applied", 面试进度: "interview_stage", 期望薪资: "salary_expect", 其他硬条件: "constraint",
};
const STATUS: Record<string, [string, "green" | "gray" | "amber" | "red"]> = {
  active: ["生效", "green"], pending: ["待确认", "amber"], superseded: ["被取代", "gray"], retracted: ["作废", "red"],
};

export default function Memory() {
  const [card, setCard] = useState<CardResp | null>(null);
  const [history, setHistory] = useState<{ label: string; versions: Version[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<CardResp>("memory").then(setCard).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function showHistory(label: string, qualifier?: string) {
    try {
      const q = qualifier ? `?qualifier=${encodeURIComponent(qualifier)}` : "";
      const r = await api<{ versions: Version[] }>(`memory/${ATTRS[label]}/history${q}`);
      setHistory({ label: label + (qualifier ? `（${qualifier}）` : ""), versions: r.versions });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function forget(f: Fact) {
    if (!confirm(`忘掉「${f.attribute}：${f.value}」？以后也不会再推断出同样的内容。`)) return;
    try {
      const params = new URLSearchParams({ value: f.value, ...(f.for ? { qualifier: f.for } : {}) });
      await api(`memory/${ATTRS[f.attribute]}?${params}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // 同一属性的几条放在一组
  const groups = new Map<string, Fact[]>();
  for (const f of card?.facts ?? []) groups.set(f.attribute, [...(groups.get(f.attribute) ?? []), f]);

  return (
    <div className="space-y-5">
      <PageHeader icon={Brain} title="记忆">
        都是你在对话里说过的（或推断后你确认过的）。每条能看来历、能删；删掉的留档，以后不会再推断出同样的内容
      </PageHeader>
      <ErrorNote error={error} />

      {card?.waiting_for_confirmation && card.waiting_for_confirmation.length > 0 && (
        <Card className="border-amber-400/50 bg-amber-500/5 space-y-2">
          <p className="font-medium flex items-center gap-2"><CircleHelp size={17} className="text-amber-500" />等你确认的</p>
          {card.waiting_for_confirmation.map((w) => (
            <p key={w.id} className="text-sm flex items-start gap-2"><Badge tone="amber">{w.id}</Badge><span>{w.statement}<span className="text-muted">（在对话里回答“对”或“不对”就行）</span></span></p>
          ))}
        </Card>
      )}

      {card === null && <div className="grid sm:grid-cols-2 gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 !rounded-2xl" />)}</div>}
      {card && card.facts.length === 0 && (
        <Card><EmptyState icon={Brain} title="还没有记住任何事">去对话里说说你想找什么样的实习：方向、城市、每周几天、能实习多久。</EmptyState></Card>
      )}
      <div className="grid sm:grid-cols-2 gap-3">
        {[...groups.entries()].map(([attr, facts], i) => (
          <Card key={attr} delay={Math.min(i, 12) * 0.04} hover className="space-y-2">
            <p className="text-xs font-medium text-muted tracking-wide">{attr}</p>
            {facts.map((f) => (
              <div key={f.id} className="group flex items-start gap-2">
                <div className="flex-1 min-w-0">
                  <p className="font-medium break-words">
                    {f.value}
                    {f.for && <span className="text-muted font-normal">（{f.scope_label ?? f.for}）</span>}
                  </p>
                  {f.valid_from && <p className="text-xs text-muted">{f.valid_from} 起</p>}
                </div>
                <div className="flex gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                  <Button variant="ghost" icon={History} onClick={() => showHistory(f.attribute, f.for)} className="!px-1.5 !py-1 text-xs">来历</Button>
                  <Button variant="danger" icon={Trash2} onClick={() => forget(f)} className="!px-1.5 !py-1 text-xs">忘掉</Button>
                </div>
              </div>
            ))}
          </Card>
        ))}
      </div>

      <FollowupList />
      <AnimatePresence>
        {history && (
          <>
            <motion.div key="mask" className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]"
                        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setHistory(null)} />
            <motion.aside key="drawer" className="fixed right-0 top-0 z-50 h-full w-full max-w-md bg-card border-l border-line shadow-2xl p-5 overflow-y-auto"
                          initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", stiffness: 320, damping: 34 }}>
              <div className="flex items-center justify-between mb-4">
                <p className="font-semibold flex items-center gap-2"><History size={17} className="text-accent" />「{history.label}」的全部版本</p>
                <Button variant="ghost" icon={X} onClick={() => setHistory(null)} className="!px-1.5" />
              </div>
              <ol className="relative border-l-2 border-line ml-2 space-y-5">
                {history.versions.map((v, i) => (
                  <motion.li key={v.id} className="pl-4 relative" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 + i * 0.06 }}>
                    <span className={`absolute -left-[7px] top-1.5 size-3 rounded-full border-2 border-card ${v.status === "active" ? "bg-emerald-500" : v.status === "pending" ? "bg-amber-500" : "bg-line"}`} />
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={STATUS[v.status]?.[1] ?? "gray"}>{STATUS[v.status]?.[0] ?? v.status}</Badge>
                      <span className="font-medium">{v.value}</span>
                    </div>
                    <p className="text-xs text-muted mt-1">
                      {v.kind} · {v.recorded} 记下{v.superseded ? ` · ${v.superseded} 被取代` : ""}{v.valid_from ? ` · ${v.valid_from} 起成立` : ""} · {v.id}
                    </p>
                    <p className="text-sm text-muted mt-1.5 flex gap-1.5 bg-soft rounded-lg px-2.5 py-1.5"><Quote size={13} className="shrink-0 mt-1" />{v.from_text}</p>
                  </motion.li>
                ))}
              </ol>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
