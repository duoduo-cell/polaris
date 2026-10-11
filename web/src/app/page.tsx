"use client";

import { useEffect, useState } from "react";
import { Badge, Card, ErrorNote, Markdown } from "@/components/markdown";
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

const CHANNEL = { push: ["推给你了", "blue"], brief: ["进了简报", "gray"], drop: ["没提", "gray"] } as const;

export default function Today() {
  const [brief, setBrief] = useState<string | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [showDropped, setShowDropped] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ markdown: string | null }>("brief").then((r) => setBrief(r.markdown)).catch((e) => setError(e.message));
    api<Note[]>("notifications?limit=100").then(setNotes).catch((e) => setError(e.message));
  }, []);

  async function feedback(n: Note, value: "useful" | "dismissed") {
    await api(`notifications/${n.id}/feedback`, { body: { feedback: value } });
    setNotes((xs) => xs.map((x) => (x.id === n.id ? { ...x, feedback: value } : x)));
  }

  const shown = notes.filter((n) => showDropped || n.channel !== "drop");
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">今天</h1>
      <ErrorNote error={error} />
      <Card>
        {brief ? <Markdown text={brief} /> : <p className="text-sm text-muted">今天还没有简报。北极星每天 8:30 以后、每轮采集之后会出一份；卡片里还没有你想找的方向和城市时不出（先去对话里聊聊）。</p>}
      </Card>
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">北极星最近的决定</h2>
          <label className="text-sm text-muted flex items-center gap-1">
            <input type="checkbox" checked={showDropped} onChange={(e) => setShowDropped(e.target.checked)} />
            也看没提的
          </label>
        </div>
        {shown.length === 0 && <p className="text-sm text-muted">还没有。</p>}
        {shown.map((n) => (
          <Card key={n.id} className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={n.channel === "push" ? "blue" : "gray"}>{CHANNEL[n.channel][0]}</Badge>
              <span className="font-medium">{n.title}</span>
              <span className="text-sm text-muted">{n.company ?? ""} · {n.cite} · 分数 {n.score.toFixed(2)}</span>
            </div>
            <p className="text-sm text-muted">{n.reasons.join("；")}</p>
            {n.channel !== "drop" && (
              <div className="flex gap-2 pt-1">
                {n.feedback ? (
                  <Badge tone={n.feedback === "useful" ? "green" : "amber"}>{n.feedback === "useful" ? "你说有用" : "你说不感兴趣"}</Badge>
                ) : (
                  <>
                    <button className="text-sm rounded border border-line px-2 py-0.5 hover:bg-background" onClick={() => feedback(n, "useful")}>有用</button>
                    <button className="text-sm rounded border border-line px-2 py-0.5 hover:bg-background" onClick={() => feedback(n, "dismissed")}>不感兴趣</button>
                  </>
                )}
              </div>
            )}
          </Card>
        ))}
      </section>
    </div>
  );
}
