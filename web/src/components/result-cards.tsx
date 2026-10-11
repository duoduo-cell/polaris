"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bookmark, Check, ExternalLink, Loader2, X } from "lucide-react";
import { Badge, ErrorNote } from "@/components/markdown";
import { api, stripUntrusted } from "@/lib/client";
import { type Followup, type Question, type Result, KINDS, STATUSES } from "@/lib/results";

type AstNode = { type: string; value?: string; url?: string; children?: AstNode[] };
function referencesPlugin(results: Result[]) {
  const labels = new Map(results.map((r) => [r.id, r.display_title]));
  return function plugin() {
    return function transform(tree: AstNode) {
      function walk(parent: AstNode) {
        if (!parent.children || ["link", "linkReference", "code", "inlineCode"].includes(parent.type)) return;
        parent.children = parent.children.flatMap((node) => {
          if (node.type !== "text" || !node.value) { walk(node); return [node]; }
          const parts: AstNode[] = [];
          let start = 0;
          for (const m of node.value.matchAll(/\b[JPQ]-\d+\b/g)) {
            if (!labels.has(m[0])) continue;
            if (m.index > start) parts.push({ type: "text", value: node.value.slice(start, m.index) });
            parts.push({ type: "link", url: `#entity-${m[0]}`, children: [{ type: "text", value: labels.get(m[0]) }] });
            start = m.index + m[0].length;
          }
          if (!parts.length) return [node];
          if (start < node.value.length) parts.push({ type: "text", value: node.value.slice(start) });
          return parts;
        });
      }
      walk(tree);
    };
  };
}

function SourceList({ result }: { result: Result }) {
  return <div className="space-y-2 text-sm">
    {(result.sources ?? []).length === 0 && <p className="text-muted">来源尚未保存</p>}
    {(result.sources ?? []).map((s, i) => <div key={`${s.raw_id ?? s.url}-${i}`} className="rounded-lg border border-line p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium">{s.label ?? s.source}{s.city ? ` · ${s.city}` : ""}</span>
        {s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-accent inline-flex items-center gap-1 shrink-0">
          打开来源 <ExternalLink size={13} />
        </a> : <span className="text-muted">缺少原始链接</span>}
      </div>
      <p className="text-xs text-muted mt-1">{s.published_at ? `来源发布时间 ${s.published_at.slice(0, 10)} · ` : ""}
        {s.fetched_at ? `采集于 ${s.fetched_at.slice(0, 10)}` : "采集时间未说明"}
        {s.access === "may_require_login" ? " · 平台可能要求登录" : ""} · 页面有效性尚未核验
      </p>
    </div>)}
  </div>;
}

function QuestionEvidence({ q }: { q: Question }) {
  return <div className="rounded-lg border border-line p-3 space-y-1.5 text-sm">
    <p className="font-medium">{q.question}</p>
    <p className="text-xs text-muted">{q.company ?? "公司归属未说明"} · {q.role ?? "岗位未说明"} · {q.round ?? "轮次未说明"}
      {q.category ? ` · ${q.category}` : ""}{q.interview_date ? ` · 面试于 ${q.interview_date}` : ""}</p>
    {q.evidence ? <blockquote className="border-l-2 border-accent/30 pl-3 text-muted whitespace-pre-wrap">{q.evidence}</blockquote>
      : <p className="text-xs text-muted">题目为抽取后的表述，尚未定位到逐题原句，请结合原帖核实。</p>}
  </div>;
}

function FollowupForm({ result, changed }: { result: Result; changed: (f: Followup) => void }) {
  const initial = result.followup ?? { saved: false, status: "untracked", stage: null, note: null, next_followup: null, revision: 0 };
  const [status, setStatus] = useState(initial.status);
  const [stage, setStage] = useState(initial.stage ?? "");
  const [note, setNote] = useState(initial.note ?? "");
  const [nextDate, setNextDate] = useState(initial.next_followup ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  async function save(changes: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setError(null);
    const signature = JSON.stringify(changes);
    if (pending.current?.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
    try {
      const out = await api<Followup>(`jobs/${result.id}/followup`, { body: {
        request_id: pending.current.id, revision: initial.revision, changes,
      } });
      pending.current = null; changed(out);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const field = "rounded-lg border border-line bg-card px-3 py-2 text-sm w-full";
  return <section className="space-y-3" aria-label="岗位跟进">
    <div className="flex items-center justify-between gap-3">
      <h3 className="font-semibold">这个岗位的跟进</h3>
      <button type="button" disabled={busy} onClick={() => save({ saved: !initial.saved })}
              className="inline-flex items-center gap-1.5 text-sm text-accent disabled:opacity-50">
        <Bookmark size={15} fill={initial.saved ? "currentColor" : "none"} />{initial.saved ? "取消收藏" : "收藏岗位"}
      </button>
    </div>
    <form onSubmit={(e) => { e.preventDefault(); void save({ status, stage: stage || null, note: note || null, next_followup: nextDate || null }); }}
          className="grid sm:grid-cols-2 gap-3">
      <label className="text-xs text-muted space-y-1">投递状态
        <select aria-label="投递状态" className={field} value={status} onChange={(e) => setStatus(e.target.value)}>
          {Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted space-y-1">面试进度
        <input aria-label="面试进度" className={field} maxLength={200} value={stage} placeholder="例如：一面通过，等待二面" onChange={(e) => setStage(e.target.value)} />
      </label>
      <label className="text-xs text-muted space-y-1">下次跟进日期
        <input aria-label="下次跟进日期" type="date" className={field} value={nextDate} onChange={(e) => setNextDate(e.target.value)} />
      </label>
      <label className="text-xs text-muted space-y-1 sm:col-span-2">我的备注 / 暂不考虑的理由
        <textarea aria-label="岗位备注" className={field} maxLength={2000} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="sm:col-span-2 flex items-center gap-3">
        <button disabled={busy} type="submit" className="rounded-lg bg-accent text-white px-4 py-2 text-sm disabled:opacity-50">
          {busy ? "保存中…" : "保存进度"}
        </button>
        <span className="text-xs text-muted">保存后才会记下；查看或收藏不会标记投递。</span>
      </div>
    </form>
    <ErrorNote error={error} />
    {!!initial.history?.length && <details className="text-xs text-muted">
      <summary className="cursor-pointer">查看进度历史</summary>
      <ul className="mt-2 space-y-2">{initial.history.map((h, i) => <li key={i}>
        {h.created_at.slice(0, 10)} · {STATUSES[h.before.status]} → {STATUSES[h.after.status]}
        {h.after.stage ? ` · ${h.after.stage}` : ""}{h.before.saved !== h.after.saved ? ` · ${h.after.saved ? "收藏" : "取消收藏"}` : ""}
        {h.after.note ? <p className="mt-1">备注：{h.after.note}</p> : null}
      </li>)}</ul>
    </details>}
  </section>;
}

function ResultDialog({ reference, close, onChanged }: { reference: string; close: () => void; onChanged: (r: Result) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const el = dialog.current;
    el?.showModal();
    let active = true;
    api<Result>(`results/${reference}`).then((r) => { if (active) setDetail(r); })
      .catch((e) => { if (active) setError((e as Error).message); });
    return () => { active = false; el?.close(); };
  }, [reference]);
  return <dialog ref={dialog} onCancel={close} aria-label="结果详情"
                 className="fixed inset-0 m-auto w-[min(760px,94vw)] max-h-[90vh] rounded-2xl border border-line bg-card text-foreground p-0 backdrop:bg-black/40">
    <div className="sticky top-0 z-10 bg-card border-b border-line p-4 flex justify-between items-start gap-4">
      <div><p className="font-semibold">{detail?.display_title ?? "正在读取详情…"}</p>
        <p className="text-xs text-muted mt-1">{detail ? KINDS[detail.kind] : ""} · {reference}</p></div>
      <button onClick={close} aria-label="关闭详情" className="rounded-lg p-1.5 hover:bg-soft"><X size={18} /></button>
    </div>
    <div className="p-5 space-y-5">
      <ErrorNote error={error} />
      {!detail && !error && <p className="text-sm text-muted flex gap-2 items-center"><Loader2 size={16} className="animate-spin" />正在读取资料</p>}
      {detail && <>
        {detail.kind === "job" ? <>
          <p className="text-sm">{detail.company} · {detail.unit ?? "团队未说明"} · {detail.city ?? "城市未说明"} · {detail.type}</p>
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge>{detail.salary_text ?? "薪资未说明"}</Badge>
            <Badge>{detail.days_per_week ? `每周 ${detail.days_per_week} 天` : "到岗天数未说明"}</Badge>
            <Badge>{detail.duration_months ? `${detail.duration_months} 个月` : "实习时长未说明"}</Badge>
            <Badge>{detail.can_convert == null ? "转正未说明" : detail.can_convert ? "有转正机会" : "不提供转正"}</Badge>
          </div>
          {!!detail.requirements?.length && <section><h3 className="font-semibold mb-2">任职要求</h3>
            <ul className="list-disc pl-5 text-sm space-y-1">{detail.requirements.map((r, i) => <li key={i}>{r}</li>)}</ul></section>}
          {!!detail.conflicts?.length && <section className="text-sm space-y-2"><h3 className="font-semibold">来源信息有冲突</h3>
            {detail.conflicts.map((c, i) => <div key={i} className="rounded-lg border border-line p-3">
              <p>{c.field}：当前取值 {String(c.chosen)} · {c.rule}</p>
              {(c.alternatives ?? c.others ?? []).map((a, n) => <p key={n} className="text-muted mt-1">其他说法：{String(a.value)}{a.sources?.length ? ` · ${a.sources.join("、")}` : ""}</p>)}
            </div>)}</section>}
          <FollowupForm key={detail.id} result={detail} changed={(followup) => {
            const next = { ...detail, followup }; setDetail(next); onChanged(next);
          }} />
        </> : <>
          <div className="flex flex-wrap gap-2"><Badge>{["interview", "question", "study"].includes(detail.kind) ? detail.material_label : KINDS[detail.kind]}</Badge>
            {detail.kind === "interview" && <Badge>{detail.interview_company ?? "面试公司待核实"}</Badge>}</div>
          {detail.summary && <p className="text-sm">{detail.summary}</p>}
          {["referral", "recruitment"].includes(detail.kind) && <p className="text-sm text-muted">
            {[detail.companies?.join("、"), detail.team, detail.roles?.join("、"), detail.city].filter(Boolean).join(" · ")}
            {detail.has_referral ? " · 原帖或评论提到可内推，请到来源核实联系方式与时效" : " · 内推方式未明确"}
          </p>}
          {detail.kind === "question" && <QuestionEvidence q={{ ...detail, question: detail.question ?? detail.display_title }} />}
          {detail.question_details?.map((q, i) => <QuestionEvidence key={i} q={q} />)}
          {!!detail.comment_companies?.length && <p className="text-xs text-muted">评论还提到了 {detail.comment_companies.join("、")}；这些公司不代表正文面试归属。</p>}
          {!!detail.advice?.length && <section><h3 className="font-semibold mb-2">作者建议</h3><ul className="list-disc pl-5 text-sm space-y-1">
            {detail.advice.map((a, i) => <li key={i}>{a}</li>)}</ul></section>}
        </>}
        <section className="space-y-2"><h3 className="font-semibold">原始来源</h3><SourceList result={detail} /></section>
        <p className="text-xs text-muted">{detail.posted_at ? `岗位发布于 ${detail.posted_at}` : detail.kind === "job" ? "岗位发布日期未说明" : ""}
          {detail.published ? `帖子发布于 ${detail.published}；实际面试日期以原文为准` : ""}
          {detail.last_seen ? ` · 最近采集于 ${detail.last_seen}` : ""}{detail.deadline ? ` · 截止 ${detail.deadline}` : ""}
          {detail.status === "gone" ? " · 最近采集时已下架" : ""}</p>
        {(detail.jd_text || detail.text) && <details className="text-sm"><summary className="cursor-pointer text-accent">查看采集原文</summary>
          <div className="whitespace-pre-wrap mt-3 text-muted">{stripUntrusted(detail.jd_text || detail.text)}</div></details>}
      </>}
    </div>
  </dialog>;
}

export function ResultCards({ results, text }: { results: Result[]; text?: string }) {
  const [opened, setOpened] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, Result>>({});
  const cards = results.map((r) => overrides[r.id] ?? r);
  return <>
    {text && <div className="prose max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm, referencesPlugin(cards)]}
      components={{ a: ({ href, children }) => href?.startsWith("#entity-")
        ? <button type="button" onClick={() => setOpened(href.slice(8))} className="text-accent underline text-left">{children}</button>
        : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>{text}</ReactMarkdown></div>}
    {!!cards.length && <div className="grid gap-3 sm:grid-cols-2" data-testid="result-cards">
      {cards.map((r) => {
        const source = r.sources?.find((s) => s.url);
        return <article key={r.id} data-testid={`result-${r.id}`} className="rounded-xl border border-line bg-card p-4 space-y-2">
          <div className="flex flex-wrap gap-1.5"><Badge>{KINDS[r.kind]}</Badge>
            {r.material_label && ["interview", "question", "study"].includes(r.kind) && <Badge>{r.material_label}</Badge>}
            {r.followup?.saved && <Badge><Bookmark size={10} className="inline mr-1" />已收藏</Badge>}
            {r.followup && r.followup.status !== "untracked" && <Badge><Check size={10} className="inline mr-1" />{STATUSES[r.followup.status]}</Badge>}
          </div>
          <h3 className="font-semibold text-sm leading-relaxed">{r.display_title}</h3>
          <p className="text-xs text-muted">{r.kind === "job"
            ? [r.unit, r.city ?? "城市未说明", r.type, r.days_per_week ? `每周 ${r.days_per_week} 天` : "到岗天数未说明", r.duration_months ? `${r.duration_months} 个月` : null].filter(Boolean).join(" · ")
            : [r.interview_company || r.company || r.companies?.join("、") || "公司待核实", r.role ?? r.roles?.join("、"), r.round ?? r.rounds?.join("、"), r.published].filter(Boolean).join(" · ")}</p>
          {r.kind === "job" && <p className="text-xs text-muted">{r.salary_text ?? "薪资未说明"}{r.deadline ? ` · 截止 ${r.deadline}` : ""}</p>}
          {r.kind !== "job" && r.summary && <p className="text-sm text-muted line-clamp-3">{r.summary}</p>}
          {!!r.conflicting_fields?.length && <p className="text-xs text-amber-600">来源说法不一致：{r.conflicting_fields.join("、")}</p>}
          <div className="flex items-center flex-wrap gap-4 pt-1 text-xs">
            <button type="button" onClick={() => setOpened(r.id)} className="text-accent font-medium">查看详情</button>
            {source?.url ? <a href={source.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent">
              {source.label ?? source.source} <ExternalLink size={12} />
            </a> : <span className="text-muted">来源链接缺失</span>}
          </div>
        </article>;
      })}
    </div>}
    {opened && <ResultDialog key={opened} reference={opened} close={() => setOpened(null)} onChanged={(r) => setOverrides((old) => ({ ...old, [r.id]: r }))} />}
  </>;
}

export function FollowupList() {
  const [rows, setRows] = useState<Result[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; api<Result[]>("followups").then((r) => { if (active) setRows(r); })
    .catch((e) => { if (active) setError((e as Error).message); }); return () => { active = false; }; }, []);
  return <section className="space-y-3"><h2 className="font-semibold">我保存的岗位和跟进</h2>
    <p className="text-sm text-muted">同一家公司的不同岗位分别记录；收藏与投递状态分开保存。</p>
    <ErrorNote error={error} />{rows.length ? <ResultCards results={rows} /> : <p className="text-sm text-muted">在岗位详情中收藏或保存进度后，会出现在这里。</p>}
  </section>;
}
