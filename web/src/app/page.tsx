"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { BellRing, Check, EyeOff, Inbox, MessageCircle, Newspaper, Sun, ThumbsDown, ThumbsUp } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorNote, Markdown, PageHeader, Skeleton } from "@/components/markdown";
import { api } from "@/lib/client";

type Note = {
  id: number;
  cite: string;
  title: string;
  company: string | null;
  channel: "push" | "brief" | "drop";
  score: number;
  reasons: string[];
  feedback: "useful" | "dismissed" | null;
  created_at: string;
};

const CHANNEL = {
  push: { label: "推给你了", tone: "blue", icon: BellRing },
  brief: { label: "进了简报", tone: "violet", icon: Newspaper },
  drop: { label: "没提", tone: "gray", icon: EyeOff },
} as const;

function Stat({ icon: Icon, label, value, delay }: { icon: typeof Sun; label: string; value: number; delay: number }) {
  return (
    <Card delay={delay} hover className="flex items-center gap-3">
      <div className="grid place-items-center size-10 rounded-xl bg-accent-soft text-accent"><Icon size={19} /></div>
      <div>
        <motion.p key={value} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="text-2xl font-bold leading-none">{value}</motion.p>
        <p className="text-xs text-muted mt-1">{label}</p>
      </div>
    </Card>
  );
}

function ScoreBar({ score }: { score: number }) {
  return (
    <div className="h-1.5 w-20 rounded-full bg-soft overflow-hidden" title={`分数 ${score.toFixed(2)}`}>
      <motion.div className="h-full rounded-full bg-gradient-to-r from-accent to-accent-2"
                  initial={{ width: 0 }} animate={{ width: `${Math.min(100, score * 100)}%` }} transition={{ duration: 0.6, ease: "easeOut" }} />
    </div>
  );
}

export default function Today() {
  const [brief, setBrief] = useState<string | null | undefined>(undefined);
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [showDropped, setShowDropped] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ markdown: string | null }>("brief").then((r) => setBrief(r.markdown)).catch((e) => { setBrief(null); setError(e.message); });
    api<Note[]>("notifications?limit=100").then(setNotes).catch((e) => { setNotes([]); setError(e.message); });
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
  return (
    <div className="space-y-6">
      <PageHeader icon={Sun} title="今天" right={
        <Link href="/chat"><Button icon={MessageCircle}>去对话</Button></Link>
      }>
        <span suppressHydrationWarning>{greet}。</span>北极星替你盯着新岗位、帖子和面经，值得说的才说
      </PageHeader>
      <ErrorNote error={error} />

      <div className="grid grid-cols-3 gap-3">
        <Stat icon={BellRing} label="推给你的" value={all.filter((n) => n.channel === "push").length} delay={0} />
        <Stat icon={Newspaper} label="进简报的" value={all.filter((n) => n.channel === "brief").length} delay={0.05} />
        <Stat icon={EyeOff} label="看过没提的" value={all.filter((n) => n.channel === "drop").length} delay={0.1} />
      </div>

      <Card delay={0.12}>
        <div className="flex items-center gap-2 mb-2 text-sm font-semibold"><Newspaper size={16} className="text-accent" />今日简报</div>
        {brief === undefined ? (
          <div className="space-y-2.5 py-1"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-3.5 w-full" /><Skeleton className="h-3.5 w-5/6" /><Skeleton className="h-3.5 w-2/3" /></div>
        ) : brief ? <Markdown text={brief} /> : (
          <EmptyState icon={Inbox} title="今天还没有简报">
            每天 8:30 以后、每轮采集之后会出一份；记忆里还没有你想找的方向和城市时不出——先去对话里聊聊。
          </EmptyState>
        )}
      </Card>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">北极星最近的决定</h2>
          <label className="text-sm text-muted flex items-center gap-2 cursor-pointer select-none">
            <span className={`relative inline-block w-8 h-[18px] rounded-full transition-colors ${showDropped ? "bg-accent" : "bg-line"}`}>
              <motion.span className="absolute top-[2px] size-[14px] rounded-full bg-white shadow" animate={{ left: showDropped ? 16 : 2 }}
                           transition={{ type: "spring", stiffness: 500, damping: 30 }} />
            </span>
            <input type="checkbox" className="sr-only" checked={showDropped} onChange={(e) => setShowDropped(e.target.checked)} />
            也看没提的
          </label>
        </div>
        {notes === null && [0, 1, 2].map((i) => <Skeleton key={i} className="h-20 w-full !rounded-2xl" />)}
        {notes !== null && shown.length === 0 && (
          <Card><EmptyState icon={BellRing} title="还没有决定">采集跑过一轮、记忆里有了你的条件之后，这里会列出它推了什么、为什么。</EmptyState></Card>
        )}
        <AnimatePresence initial={false}>
          {shown.map((n, i) => {
            const c = CHANNEL[n.channel];
            return (
              <motion.div key={n.id} layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }}
                          transition={{ duration: 0.25, delay: Math.min(i, 10) * 0.035 }}>
                <div className={`rounded-2xl border bg-card p-4 space-y-2 hover:shadow-[0_8px_24px_rgba(16,24,40,0.08)] transition-shadow ${n.channel === "push" ? "border-accent/40" : "border-line"}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={c.tone} icon={c.icon}>{c.label}</Badge>
                    <span className="font-medium">{n.title}</span>
                    <span className="text-sm text-muted">{n.company ?? ""}</span>
                    <span className="ml-auto flex items-center gap-2 text-xs text-muted"><ScoreBar score={n.score} />{n.score.toFixed(2)}</span>
                  </div>
                  <p className="text-sm text-muted">{n.reasons.join("；")} <span className="opacity-60">· {n.cite}</span></p>
                  {n.channel !== "drop" && (
                    <div className="flex gap-1">
                      {n.feedback ? (
                        <motion.span initial={{ scale: 0.8 }} animate={{ scale: 1 }}>
                          <Badge tone={n.feedback === "useful" ? "green" : "amber"} icon={Check}>{n.feedback === "useful" ? "你说有用" : "你说不感兴趣"}</Badge>
                        </motion.span>
                      ) : (
                        <>
                          <Button variant="ghost" icon={ThumbsUp} onClick={() => feedback(n, "useful")} className="!px-2 !py-1 text-xs">有用</Button>
                          <Button variant="ghost" icon={ThumbsDown} onClick={() => feedback(n, "dismissed")} className="!px-2 !py-1 text-xs">不感兴趣</Button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </section>
    </div>
  );
}
