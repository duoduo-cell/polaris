"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { Brain, CircleHelp, History, MessageCircle, Quote, Trash2, X } from "lucide-react";
import { Badge, Button, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { FollowupList } from "@/components/result-cards";
import { api } from "@/lib/client";
import { hideRefs } from "@/lib/refs";

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
const SECTIONS = [
  { title: "想找什么", keys: ["target_role", "target_city", "job_type", "company_like", "company_avoid", "salary_expect", "constraint"] },
  { title: "时间", keys: ["available_from", "days_per_week", "duration_months", "grad_year"] },
  { title: "背景", keys: ["school", "degree", "major", "skill", "project"] },
  { title: "投递和面试", keys: ["applied", "interview_stage"] },
];
const UNITS: Record<string, string> = { days_per_week: "天", duration_months: "个月" };
const NEEDED: [string, string][] = [["target_role", "想找的方向"], ["target_city", "城市"], ["days_per_week", "每周能去几天"],
  ["duration_months", "能实习多久"], ["skill", "会的技术"]];
const STATUS: Record<string, [string, "green" | "gray" | "amber" | "red"]> = {
  active: ["生效", "green"], pending: ["待确认", "amber"], superseded: ["被取代", "gray"], retracted: ["作废", "red"],
};

export default function Memory() {
  const [card, setCard] = useState<CardResp | null>(null);
  const [history, setHistory] = useState<{ fact: Fact; label: string; versions: Version[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<CardResp>("memory").then((r) => setCard({ ...r, facts: r.facts ?? [] })).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function showHistory(f: Fact) {
    try {
      const q = f.for ? `?qualifier=${encodeURIComponent(f.for)}` : "";
      const r = await api<{ versions: Version[] }>(`memory/${ATTRS[f.attribute]}/history${q}`);
      setHistory({ fact: f, label: f.attribute + (f.for ? `（${hideRefs(f.scope_label ?? f.for)}）` : ""), versions: r.versions });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function forget(f: Fact) {
    if (!confirm(`忘掉「${f.attribute}：${hideRefs(f.value)}」？以后也不会再推断出同样的内容。`)) return;
    try {
      const params = new URLSearchParams({ value: f.value, ...(f.for ? { qualifier: f.for } : {}) });
      await api(`memory/${ATTRS[f.attribute]}?${params}`, { method: "DELETE" });
      setHistory(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const facts = card?.facts ?? [];
  const by = new Map<string, Fact[]>();
  for (const f of facts) by.set(ATTRS[f.attribute] ?? f.attribute, [...(by.get(ATTRS[f.attribute] ?? f.attribute) ?? []), f]);
  const chip = (key: string, joiner = "、") => {
    const list = by.get(key);
    if (!list?.length) return null;
    return list.map((f, i) => <span key={f.id}>{i > 0 && joiner}
      <button type="button" onClick={() => showHistory(f)}
              className="mx-px rounded px-1 py-px leading-normal align-baseline text-accent bg-accent-soft hover:underline underline-offset-2">{hideRefs(f.value)}</button>
    </span>);
  };
  const missing = NEEDED.filter(([k]) => !by.has(k)).map(([, label]) => label);
  // 几个短句用逗号连起来，句号收尾；没记住的部分不出现
  const sentence = (parts: (React.ReactNode | false)[]) => {
    const kept = parts.filter(Boolean);
    return kept.length ? <p>{kept.map((p, i) => <span key={i}>{i > 0 && "，"}{p}</span>)}。</p> : null;
  };

  return (
    <div className="space-y-8">
      <PageHeader title="关于你">
        都是你在对话里说过的，或推断后你确认过的。点一个值能看它从哪句话来、改过几次；不对就删掉，以后不会再这样推断。
      </PageHeader>
      <ErrorNote error={error} />

      {card === null ? <Skeleton className="h-28 !rounded-xl" /> : facts.length === 0 ? (
        <div className="rounded-xl border border-line bg-card">
          <EmptyState icon={Brain} title="还没记住任何事"
                      action={<Link href="/chat"><Button variant="primary" icon={MessageCircle}>去对话里说说</Button></Link>}>
            说说你想找什么样的实习：方向、城市、每周能去几天、能实习多久、会哪些技术。推荐和推送都按这个来。
          </EmptyState>
        </div>
      ) : (
        <motion.section initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                        className="rounded-xl border border-line bg-card px-5 py-4 text-[15px] leading-[2.1]">
          {(by.has("target_city") || by.has("target_role") || by.has("job_type")) && <p>
            你想{by.has("target_city") && <>在{chip("target_city")}</>}找{chip("target_role") ?? "实习"}{by.has("job_type") && <>的{chip("job_type")}</>}。
          </p>}
          {sentence([by.has("available_from") && <>{chip("available_from")}起能开始</>,
            by.has("days_per_week") && <>每周能去{chip("days_per_week")}天</>,
            by.has("duration_months") && <>能实习{chip("duration_months")}个月</>,
            by.has("grad_year") && <>{chip("grad_year")}年毕业</>])}
          {sentence([(by.has("school") || by.has("degree") || by.has("major")) && <>{by.has("school") && <>在{chip("school")}</>}
              {by.has("degree") && chip("degree")}{by.has("major") && <>读{chip("major")}</>}</>,
            by.has("skill") && <>会{chip("skill")}</>,
            by.has("project") && <>做过{chip("project", "；")}</>])}
          {sentence([by.has("company_like") && <>想去{chip("company_like")}</>, by.has("company_avoid") && <>不想去{chip("company_avoid")}</>,
            by.has("constraint") && <>{chip("constraint")}</>, by.has("salary_expect") && <>期望{chip("salary_expect")}</>])}
          {missing.length > 0 && <p className="text-sm text-muted mt-1 leading-relaxed">
            还不知道：{missing.join("、")}。知道了才能逐条判断岗位合不合适。<Link href="/chat" className="text-accent hover:underline">去说说</Link>
          </p>}
        </motion.section>
      )}

      {card?.waiting_for_confirmation && card.waiting_for_confirmation.length > 0 && (
        <section className="rounded-xl border border-warn/40 bg-warn-soft px-5 py-4 space-y-2">
          <p className="font-medium flex items-center gap-2 text-warn"><CircleHelp size={16} aria-hidden />等你确认</p>
          {card.waiting_for_confirmation.map((w) => <p key={w.id} className="text-sm">{hideRefs(w.statement)}</p>)}
          <p className="text-xs text-muted">在对话里回答“对”或“不对”就行。确认前不会当真。</p>
        </section>
      )}

      {facts.length > 0 && <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted">全部记录</h2>
        <div className="grid md:grid-cols-2 gap-3">
          {SECTIONS.filter((s) => s.keys.some((k) => by.has(k))).map((s, i) => (
            <motion.div key={s.title} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
                        className="rounded-xl border border-line bg-card px-4 py-3">
              <p className="text-xs font-medium text-muted mb-1.5">{s.title}</p>
              <ul className="divide-y divide-line">
                {s.keys.flatMap((k) => by.get(k) ?? []).map((f) => (
                  <li key={f.id} className="group flex items-center gap-3 py-2 text-sm">
                    <span className="w-28 shrink-0 text-muted">{f.attribute.replace("（月）", "")}{f.for ? <span className="block text-xs text-faint truncate">{hideRefs(f.scope_label ?? f.for)}</span> : null}</span>
                    <span className="flex-1 min-w-0 break-words">{hideRefs(f.value)}{UNITS[ATTRS[f.attribute]] && /^\d+$/.test(f.value) ? ` ${UNITS[ATTRS[f.attribute]]}` : ""}
                      {f.valid_from && <span className="text-xs text-muted">（{f.valid_from} 起）</span>}</span>
                    <span className="flex gap-0.5 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                      <Button variant="ghost" icon={History} label="来历" title="看来历" onClick={() => showHistory(f)} className="!px-1.5 !py-1" />
                      <Button variant="danger" icon={Trash2} label="忘掉" title="忘掉" onClick={() => forget(f)} className="!px-1.5 !py-1" />
                    </span>
                  </li>
                ))}
              </ul>
            </motion.div>
          ))}
        </div>
      </section>}

      <FollowupList />

      <AnimatePresence>
        {history && (
          <>
            <motion.div key="mask" className="fixed inset-0 z-40 bg-black/20"
                        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setHistory(null)} />
            <motion.aside key="drawer" role="dialog" aria-label="记忆的来历"
                          className="fixed right-0 top-0 z-50 h-full w-full max-w-md bg-card border-l border-line p-5 overflow-y-auto"
                          initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }}
                          transition={{ type: "spring", stiffness: 380, damping: 36 }}>
              <div className="flex items-center justify-between mb-5">
                <p className="font-semibold">「{history.label}」的来历</p>
                <Button variant="ghost" icon={X} label="关闭" onClick={() => setHistory(null)} className="!px-1.5" />
              </div>
              <ol className="relative border-l border-line ml-1.5 space-y-5">
                {history.versions.map((v, i) => (
                  <motion.li key={v.id} className="pl-5 relative" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.08 + i * 0.05 }}>
                    <span className={`absolute -left-[5px] top-2 size-2.5 rounded-full ring-4 ring-card ${v.status === "active" ? "bg-ok" : v.status === "pending" ? "bg-warn" : "bg-faint"}`} />
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={STATUS[v.status]?.[1] ?? "gray"}>{STATUS[v.status]?.[0] ?? v.status}</Badge>
                      <span className="font-medium">{hideRefs(v.value)}</span>
                    </div>
                    <p className="text-xs text-muted mt-1">
                      {v.kind} · {v.recorded} 记下{v.superseded ? ` · ${v.superseded} 被取代` : ""}{v.valid_from ? ` · ${v.valid_from} 起成立` : ""}
                    </p>
                    <p className="text-sm text-muted mt-2 flex gap-1.5 rounded-lg bg-soft px-3 py-2"><Quote size={13} className="shrink-0 mt-1" aria-hidden />{v.from_text}</p>
                  </motion.li>
                ))}
              </ol>
              <div className="mt-6 border-t border-line pt-4">
                <Button variant="danger" icon={Trash2} onClick={() => forget(history.fact)}>忘掉「{hideRefs(history.fact.value)}」</Button>
                <p className="text-xs text-muted mt-2">忘掉后旧记录留档，以后不会再推断出同样的内容。</p>
              </div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
