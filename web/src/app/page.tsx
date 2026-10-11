"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import {
  BellRing, CalendarClock, Check, CircleCheckBig, EyeOff, Hand, Inbox, MessageCircle, Newspaper, Radar, ThumbsDown, ThumbsUp,
} from "lucide-react";
import { Badge, Button, EmptyState, ErrorNote, Markdown, Skeleton } from "@/components/markdown";
import { type Agenda, type AgendaItem } from "@/components/nav";
import { ResultPanel } from "@/components/result-cards";
import { api } from "@/lib/client";
import { hideRefs } from "@/lib/refs";

type Note = {
  id: number; cite: string; title: string; company: string | null; channel: "push" | "brief" | "drop";
  score: number; reasons: string[]; feedback: "useful" | "dismissed" | null; created_at: string;
};

const CHANNEL = {
  push: { label: "推给你了", tone: "blue", icon: BellRing },
  brief: { label: "进了简报", tone: "gray", icon: Newspaper },
  drop: { label: "没提", tone: "gray", icon: EyeOff },
} as const;
const GROUPS = [
  { key: "doing", title: "正在做", icon: Radar, empty: "现在没在忙" },
  { key: "needs_you", title: "需要你", icon: Hand, empty: "没有要你决定的事" },
  { key: "scheduled", title: "已安排", icon: CalendarClock, empty: "没有安排" },
  { key: "done", title: "今天做完的", icon: CircleCheckBig, empty: "今天还没做什么" },
] as const;
const NO_REFS = new Map<string, string>();

function when(item: AgendaItem): string | null {
  if (!item.when) return null;
  const m = item.when.match(/^\d{4}-(\d{2})-(\d{2})$/);
  return m ? `${Number(m[1])} 月 ${Number(m[2])} 日` : item.when;
}

function AgendaGroup({ title, icon: Icon, items, empty, index, open }: {
  title: string; icon: typeof Radar; items: AgendaItem[]; empty: string; index: number; open: (ref: string) => void;
}) {
  const urgent = title === "需要你" && items.length > 0;
  return (
    <motion.section initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.05 }}
                    className={`rounded-xl border bg-card p-4 ${urgent ? "border-warn/40" : "border-line"}`}>
      <h3 className={`flex items-center gap-2 text-sm font-medium ${urgent ? "text-warn" : ""}`}>
        <Icon size={15} aria-hidden className={urgent ? "" : "text-muted"} />{title}
        {items.length > 0 && <span className="text-xs text-faint font-normal">{items.length}</span>}
      </h3>
      {items.length === 0 ? <p className="mt-2 text-sm text-faint">{empty}</p> : (
        <ul className="mt-2.5 space-y-2">
          {items.map((item, i) => {
            const body = <>
              {item.kind === "watch" || item.kind === "chat" ? <span className="live-dot mt-[7px] shrink-0" aria-hidden />
                : <span className="mt-[8px] size-1.5 rounded-full bg-faint shrink-0" aria-hidden />}
              <span className="min-w-0">
                <span className="block text-sm leading-relaxed">{hideRefs(item.text)}</span>
                {(item.detail || when(item)) && <span className="block text-xs text-muted">{[when(item), item.detail].filter(Boolean).join(" · ")}</span>}
              </span>
            </>;
            return (
              <motion.li key={i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: index * 0.05 + i * 0.04 }}>
                {item.job ? <button type="button" onClick={() => open(item.job!)} className="flex gap-2.5 text-left hover:text-accent">{body}</button>
                  : item.href ? <Link href={item.href} className="flex gap-2.5 hover:text-accent">{body}</Link>
                  : <div className="flex gap-2.5">{body}</div>}
              </motion.li>
            );
          })}
        </ul>
      )}
    </motion.section>
  );
}

export default function Today() {
  const [agenda, setAgenda] = useState<Agenda | null>(null);
  const [brief, setBrief] = useState<string | null | undefined>(undefined);
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [showDropped, setShowDropped] = useState(false);
  const [opened, setOpened] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Agenda>("agenda").then(setAgenda).catch(() => setAgenda({}));
    api<{ markdown: string | null }>("brief").then((r) => setBrief(r.markdown ?? null)).catch((e) => { setBrief(null); setError(e.message); });
    api<Note[]>("notifications?limit=100").then((r) => setNotes(Array.isArray(r) ? r : [])).catch((e) => { setNotes([]); setError(e.message); });
  }, []);

  async function feedback(n: Note, value: "useful" | "dismissed") {
    try {
      await api(`notifications/${n.id}/feedback`, { body: { feedback: value } });
      setNotes((xs) => xs && xs.map((x) => (x.id === n.id ? { ...x, feedback: value } : x)));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const all = notes ?? [];
  const shown = all.filter((n) => showDropped || n.channel !== "drop");
  const hour = new Date().getHours();
  const greet = hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const needsProfile = agenda?.needs_you?.some((n) => n.kind === "profile");
  return (
    <div className="space-y-8">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[1.6rem] font-semibold tracking-tight" suppressHydrationWarning>{greet}</h1>
          <p className="text-sm text-muted mt-1">北极星替你盯着新岗位、帖子和面经，值得说的才说；要你拍板的事放在最前面。</p>
        </div>
        <Link href="/chat" className="shrink-0"><Button variant="primary" icon={MessageCircle}>去对话</Button></Link>
      </div>
      <ErrorNote error={error} />

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted">北极星在做的事</h2>
        {agenda === null ? <div className="grid md:grid-cols-2 gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28 !rounded-xl" />)}</div> : (
          <div className="grid md:grid-cols-2 gap-3">
            {GROUPS.map((g, i) => <AgendaGroup key={g.key} title={g.title} icon={g.icon} empty={g.empty} index={i}
                                               items={agenda[g.key] ?? []} open={setOpened} />)}
          </div>
        )}
        {needsProfile && <p className="text-sm text-muted">还不知道你想找什么，所以不推送、也不出简报。<Link href="/chat" className="text-accent hover:underline">去对话里说说</Link></p>}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted flex items-center gap-2">今日简报</h2>
        <div className="rounded-xl border border-line bg-card px-5 py-4">
          {brief === undefined ? (
            <div className="space-y-2.5 py-1"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-3.5 w-full" /><Skeleton className="h-3.5 w-5/6" /></div>
          ) : brief ? <Markdown text={brief} refs={NO_REFS} /> : (
            <EmptyState icon={Inbox} title="今天还没有简报">每天 8:30 以后、每轮采集之后会出一份；还不知道你想找什么时不出。</EmptyState>
          )}
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted">最近的推送决定</h2>
          <label className="text-xs text-muted flex items-center gap-2 cursor-pointer select-none">
            <span className={`relative inline-block w-7 h-4 rounded-full transition-colors ${showDropped ? "bg-foreground" : "bg-line"}`}>
              <motion.span className="absolute top-[2px] size-3 rounded-full bg-card shadow" animate={{ left: showDropped ? 14 : 2 }}
                           transition={{ type: "spring", stiffness: 500, damping: 30 }} />
            </span>
            <input type="checkbox" className="sr-only" checked={showDropped} onChange={(e) => setShowDropped(e.target.checked)} />
            也看没提的
          </label>
        </div>
        {notes === null && [0, 1].map((i) => <Skeleton key={i} className="h-16 w-full !rounded-xl" />)}
        {notes !== null && shown.length === 0 && (
          <p className="text-sm text-faint">还没有。采集跑过一轮、北极星知道你想找什么之后，这里会列出它推了什么、为什么。</p>
        )}
        <div className="rounded-xl border border-line bg-card divide-y divide-line overflow-hidden empty:hidden">
          <AnimatePresence initial={false}>
            {shown.map((n, i) => {
              const c = CHANNEL[n.channel];
              return (
                <motion.div key={n.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                            transition={{ duration: 0.2, delay: Math.min(i, 10) * 0.03 }} className="px-4 py-3 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={c.tone} icon={c.icon}>{c.label}</Badge>
                    <button type="button" onClick={() => setOpened(n.cite)} className="font-medium text-left hover:text-accent">{hideRefs(n.title)}</button>
                    {n.company && <span className="text-sm text-muted">{n.company}</span>}
                    <span className="ml-auto text-xs text-faint tabular-nums" title="综合分：和你的匹配、新不新、急不急">{Math.round(n.score * 100)}</span>
                  </div>
                  <p className="text-sm text-muted">{hideRefs(n.reasons.join("；"))}</p>
                  {n.channel !== "drop" && (
                    <div className="flex gap-1 -ml-1.5">
                      {n.feedback ? (
                        <Badge tone={n.feedback === "useful" ? "green" : "amber"} icon={Check}>{n.feedback === "useful" ? "你说有用" : "你说不感兴趣"}</Badge>
                      ) : (
                        <>
                          <Button variant="ghost" icon={ThumbsUp} onClick={() => feedback(n, "useful")} className="!px-1.5 !py-0.5 text-xs">有用</Button>
                          <Button variant="ghost" icon={ThumbsDown} onClick={() => feedback(n, "dismissed")} className="!px-1.5 !py-0.5 text-xs">不感兴趣</Button>
                        </>
                      )}
                    </div>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </section>
      {opened && <ResultPanel key={opened} reference={opened} close={() => setOpened(null)} />}
    </div>
  );
}
