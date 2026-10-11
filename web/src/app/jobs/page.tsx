"use client";

import { useState } from "react";
import { Badge, Card, ErrorNote } from "@/components/markdown";
import { stripUntrusted, tool } from "@/lib/client";

type JobRow = {
  id: string; company: string; title: string; city: string | null; type: string | null;
  posted_at?: string; deadline?: string; listings?: number; conflicting_fields?: string[];
};
type JobDetail = JobRow & {
  unit?: string; directions?: string[]; requirements?: string[]; jd_text?: string;
  sources?: { source: string; url: string | null; city: string | null; fetched_at: string }[];
  conflicts?: { field: string; chosen: unknown; rule: string; others: { value: unknown; sources: string[] }[] }[];
};

export default function Jobs() {
  const [form, setForm] = useState({ query: "", company: "", city: "", job_type: "" });
  const [rows, setRows] = useState<JobRow[] | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    setBusy(true); setError(null); setDetail(null);
    const args: Record<string, unknown> = { limit: 30 };
    for (const [k, v] of Object.entries(form)) if (v.trim()) args[k] = v.trim();
    try {
      const r = await tool<{ items: JobRow[] }>("jobs_search", args);
      setRows(r.items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setError(null);
    try {
      setDetail(await tool<JobDetail>("jobs_get", { job_id: id, response_format: "detailed" }));
    } catch (e) {
      setError((e as Error).message);
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
      <div className="grid md:grid-cols-2 gap-4">
        <div className="space-y-2">
          {rows?.length === 0 && <p className="text-sm text-muted">没有符合条件的岗位。</p>}
          {rows?.map((r) => (
            <button key={r.id} onClick={() => open(r.id)} className="block w-full text-left rounded-lg border border-line bg-card p-3 hover:border-accent">
              <p className="font-medium">{r.title}</p>
              <p className="text-sm text-muted">
                {r.company} · {r.city ?? "—"} · {r.type ?? "—"} · {r.id}{r.posted_at ? ` · ${r.posted_at}` : ""}
              </p>
              <div className="flex gap-1 mt-1">
                {r.listings && <Badge tone="blue">合并了 {r.listings} 条挂牌</Badge>}
                {r.conflicting_fields && <Badge tone="amber">说法不一致：{r.conflicting_fields.join("、")}</Badge>}
              </div>
            </button>
          ))}
        </div>
        {detail && (
          <Card className="space-y-3 md:sticky md:top-4 self-start max-h-[85vh] overflow-y-auto">
            <div>
              <p className="font-semibold">{detail.title}</p>
              <p className="text-sm text-muted">{detail.company}{detail.unit ? ` · ${detail.unit}` : ""} · {detail.city} · {detail.type} · {detail.id}</p>
            </div>
            {detail.conflicts && detail.conflicts.length > 0 && (
              <div className="text-sm space-y-1">
                <p className="font-medium">各处说法不一致的字段</p>
                {detail.conflicts.map((c, i) => (
                  <p key={i}>
                    {c.field}：取 <b>{String(c.chosen)}</b>（{c.rule}）；另一种说法{" "}
                    {c.others.map((o) => `${String(o.value)}（${o.sources.join("、")}）`).join("；")}
                  </p>
                ))}
              </div>
            )}
            <div className="text-sm">
              <p className="font-medium">来源</p>
              {detail.sources?.map((s, i) => (
                <p key={i} className="text-muted">
                  {s.source} · {s.city ?? "—"} · {s.fetched_at}
                  {s.url ? <> · <a className="text-accent underline" href={s.url} target="_blank" rel="noreferrer">原网页</a></> : null}
                </p>
              ))}
            </div>
            <pre className="text-sm whitespace-pre-wrap font-sans">{stripUntrusted(detail.jd_text)}</pre>
          </Card>
        )}
      </div>
    </div>
  );
}
