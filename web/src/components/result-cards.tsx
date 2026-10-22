"use client";

// 结果卡片（岗位、面经、面试题、内推、招聘、备考）和右侧滑出的详情面板。
// 岗位卡片先看"和你对不对得上"：匹配分、逐条条件、技能你有的和还缺的；编号不显示，来源能点开。
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { Bookmark, Check, ExternalLink, Loader2, MessageCircle, Minus, X } from "lucide-react";
import { Badge, Button, ErrorNote, Markdown, ScoreRing } from "@/components/markdown";
import { useChatSession } from "@/components/chat-session";
import { api, stripUntrusted } from "@/lib/client";
import { hideRefs } from "@/lib/refs";
import { type Followup, type Match, type Question, type Result, KINDS, STATUSES, matchSummary } from "@/lib/results";

const MARK_TONES = ["bg-accent-soft text-accent", "bg-ok-soft text-ok", "bg-warn-soft text-warn", "bg-bad-soft text-bad", "bg-soft text-muted"];

function CompanyMark({ name }: { name?: string | null }) {
  const text = (name ?? "?").replace(/[（(].*$/, "").trim();
  const tone = MARK_TONES[[...text].reduce((n, ch) => n + ch.charCodeAt(0), 0) % MARK_TONES.length];
  return <div aria-hidden className={`grid place-items-center size-9 shrink-0 rounded-lg text-[13px] font-semibold ${tone}`}>{[...text][0] ?? "?"}</div>;
}

function CheckPill({ ok, children }: { ok: boolean | null; children: React.ReactNode }) {
  const style = ok ? "bg-ok-soft text-ok" : ok === false ? "bg-bad-soft text-bad" : "bg-soft text-muted";
  const Icon = ok ? Check : ok === false ? X : Minus;
  return <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs ${style}`}><Icon size={11} strokeWidth={2.6} aria-hidden />{children}</span>;
}

function SkillPills({ match, limit = 6 }: { match: Match; limit?: number }) {
  const { have, missing, required, known } = match.skills;
  const shown = known ? [...have.map((s) => [s, true] as const), ...missing.map((s) => [s, false] as const)]
    : required.map((s) => [s, null] as const);
  if (!shown.length) return null;
  return <div className="flex flex-wrap gap-1.5">
    {shown.slice(0, limit).map(([s, ok]) => <span key={s} className={`rounded-md px-1.5 py-0.5 text-xs ${ok ? "border border-line text-foreground"
      : ok === false ? "border border-dashed border-faint text-muted" : "bg-soft text-muted"}`}>{ok === false ? `待确认 ${s}` : s}</span>)}
    {shown.length > limit && <span className="text-xs text-faint self-center">等 {shown.length} 项</span>}
  </div>;
}

function when(r: Result): string | null {
  for (const [value, label] of [[r.posted_at, "岗位日期"], [r.published, "资料日期"], [r.last_seen, "最近采集"]] as const) {
    const day = sourceDay(value);
    if (day) return `${label} ${day}`;
  }
  return null;
}

function sourceDay(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
}

function SourceList({ result }: { result: Result }) {
  return <div className="space-y-2 text-sm">
    {(result.sources ?? []).length === 0 && <p className="text-muted">来源尚未保存</p>}
    {(result.sources ?? []).map((s, i) => <div key={`${s.raw_id ?? s.url}-${i}`} className="rounded-lg border border-line px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium">{s.label ?? s.source}{s.city ? <span className="text-muted font-normal"> · {s.city}</span> : null}</span>
        {s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-accent inline-flex items-center gap-1 shrink-0 text-[13px]">
          打开来源 <ExternalLink size={12} aria-hidden />
        </a> : <span className="text-muted text-[13px]">缺少原始链接</span>}
      </div>
      <p className="text-xs text-muted mt-1">{sourceDay(s.published_at) ? `${s.date_kind === "published" ? "来源发布于" : s.date_kind === "updated" ? "来源更新于" : "来源日期"} ${sourceDay(s.published_at)} · ` : ""}
        {sourceDay(s.fetched_at) ? `这份原文采集于 ${sourceDay(s.fetched_at)}` : "原文采集时间未说明"}
        {s.access === "may_require_login" ? " · 平台可能要求登录" : ""} · 页面现在是否还在没有核验
      </p>
    </div>)}
  </div>;
}

function QuestionEvidence({ q }: { q: Question }) {
  return <div className="rounded-lg border border-line p-3 space-y-1.5 text-sm">
    <p className="font-medium">{q.question}</p>
    <p className="text-xs text-muted">{q.company ?? "公司归属未说明"} · {q.role ?? "岗位未说明"} · {q.round ?? "轮次未说明"}
      {q.category ? ` · ${q.category}` : ""}{q.interview_date ? ` · 面试于 ${q.interview_date}` : ""}</p>
    {q.evidence ? <blockquote className="border-l-2 border-line pl-3 text-muted whitespace-pre-wrap">{q.evidence}</blockquote>
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
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  async function save(changes: Record<string, unknown>, form = false) {
    if (busy) return;
    setBusy(true); setError(null); setSaved(false);
    const signature = JSON.stringify(changes);
    if (pending.current?.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
    try {
      const out = await api<Followup>(`jobs/${result.id}/followup`, { body: {
        request_id: pending.current.id, revision: initial.revision, changes,
      } });
      pending.current = null; changed(out); if (form) setSaved(true);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const field = "rounded-lg border border-line bg-card px-3 py-2 text-sm w-full outline-none focus:border-accent";
  return <section className="space-y-3" aria-label="岗位跟进">
    <div className="flex items-center justify-between gap-3">
      <h3 className="font-semibold">这个岗位的跟进</h3>
      <button type="button" disabled={busy} onClick={() => save({ saved: !initial.saved })}
              className="inline-flex items-center gap-1.5 text-sm text-accent disabled:opacity-50">
        <Bookmark size={15} fill={initial.saved ? "currentColor" : "none"} aria-hidden />{initial.saved ? "取消收藏" : "收藏岗位"}
      </button>
    </div>
    <form onSubmit={(e) => { e.preventDefault(); void save({ status, stage: stage || null, note: note || null, next_followup: nextDate || null }, true); }}
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
        <Button type="submit" variant="primary" disabled={busy}>{busy ? "保存中…" : "保存进度"}</Button>
        {saved ? <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-xs text-ok inline-flex items-center gap-1"><Check size={12} aria-hidden />已保存</motion.span>
          : <span className="text-xs text-muted">保存后才会记下；查看或收藏不算投递。</span>}
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

function MatchSection({ detail, close }: { detail: Result; close: () => void }) {
  const router = useRouter();
  const { setInput } = useChatSession();
  const match = detail.match;
  const notStated = match?.skills.not_stated ?? match?.skills.missing ?? [];
  function ask(text: string) { setInput(text); close(); router.push("/chat"); }
  if (!match) {
    return <section className="rounded-xl bg-soft px-4 py-3 text-sm text-muted flex items-center justify-between gap-3">
      <span>告诉北极星你的条件（城市、每周几天、会的技术），这里会逐条显示这个岗位和你对不对得上。</span>
      <Button icon={MessageCircle} onClick={() => ask("我的求职条件是：")}>去说说</Button>
    </section>;
  }
  const name = [detail.company, detail.title].filter(Boolean).join(" · ");
  const summary = matchSummary(match);
  return <section className="space-y-3">
    {match.criteria && <p className="text-xs text-muted">本次回答时按{[
      match.criteria.city, match.criteria.direction,
      ({ intern: "实习", campus: "校招", social: "社招" } as Record<string, string>)[match.criteria.job_type ?? ""],
      match.criteria.max_days_per_week ? `每周最多${match.criteria.max_days_per_week}天` : null,
    ].filter(Boolean).join("、")}对照；其他已记录条件继续使用，当前岗位要求仍需核实。</p>}
    <div className="flex items-center gap-3">
      <ScoreRing score={match.score} size={52} />
      <div>
        <p className="font-semibold">{summary.label}</p>
        <p className="text-xs text-muted">{summary.description}</p>
      </div>
    </div>
    <ul className="grid sm:grid-cols-2 gap-1.5">
      {match.checks.map((c) => <li key={c.key} className="flex items-start gap-2 text-sm">
        <CheckPill ok={c.ok}>{c.label}</CheckPill><span className={c.ok === false ? "text-bad" : "text-muted"}>{c.detail}</span>
      </li>)}
    </ul>
    {match.skills.required.length > 0 && <div className="space-y-1.5">
      <p className="text-xs text-muted">{match.skills.known ? `岗位要的技能：已记录 ${match.skills.have.length} 项，另有 ${notStated.length} 项待确认`
        : "岗位要的技能（还不知道你会什么，跟北极星说说就能对比）"}</p>
      <SkillPills match={match} limit={20} />
      {notStated.length > 0 && <Button variant="ghost" icon={MessageCircle} className="!px-2 -ml-2 text-accent"
        onClick={() => ask(`${name} 这个岗位要求 ${notStated.slice(0, 3).join("、")}，我的掌握情况还没确认。请给我自测题和答题要点，帮我判断需要补什么。`)}>
        问北极星怎么自测
      </Button>}
    </div>}
  </section>;
}

export function ResultPanel({ reference, close, onChanged, queryMatch }: {
  reference: string; close: () => void; onChanged?: (r: Result) => void; queryMatch?: Match;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const el = dialog.current;
    el?.showModal();
    let active = true;
    api<Result>(`results/${reference}`).then((r) => { if (active) setDetail(queryMatch && r.kind === "job" ? { ...r, match: queryMatch } : r); })
      .catch((e) => { if (active) setError((e as Error).message); });
    return () => { active = false; el?.close(); };
  }, [reference, queryMatch]);
  const facts = detail?.kind === "job" ? [
    [detail.salary_text, "薪资"], [detail.days_per_week ? `每周 ${detail.days_per_week} 天` : null, "每周几天"],
    [detail.duration_months ? `${detail.duration_months} 个月` : null, "实习多久"],
    [detail.can_convert == null ? null : detail.can_convert ? "有转正机会" : "不提供转正", "能否转正"],
    [detail.deadline ? `截止 ${detail.deadline}` : null, "截止日期"],
  ] as const : [];
  return <dialog ref={dialog} onCancel={close} onClick={(e) => { if (e.target === dialog.current) close(); }} aria-label="结果详情" className="panel">
    <div className="flex h-full flex-col">
      <div className="flex justify-between items-start gap-4 border-b border-line px-5 py-4">
        <div className="flex gap-3 min-w-0">
          {detail?.kind === "job" && <CompanyMark name={detail.company} />}
          <div className="min-w-0">
            <p className="font-semibold leading-snug">{detail ? (detail.kind === "job" ? detail.title ?? detail.display_title : detail.display_title) : "正在读取…"}</p>
            <p className="text-xs text-muted mt-1">{detail ? (detail.kind === "job"
              ? [detail.company, detail.unit, detail.city, detail.type].filter(Boolean).join(" · ") : KINDS[detail.kind]) : ""}</p>
          </div>
        </div>
        <button onClick={close} aria-label="关闭详情" className="rounded-lg p-1.5 text-muted hover:bg-soft hover:text-foreground"><X size={18} /></button>
      </div>
      <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">
        <ErrorNote error={error} />
        {!detail && !error && <p className="text-sm text-muted flex gap-2 items-center"><Loader2 size={16} className="animate-spin" />正在读取资料</p>}
        {detail && <>
          {detail.kind === "job" ? <>
            <MatchSection detail={detail} close={close} />
            <div className="flex flex-wrap gap-1.5">
              {facts.filter(([v]) => v).map(([v]) => <Badge key={v}>{v}</Badge>)}
              {facts.some(([v]) => !v) && <span className="text-xs text-faint self-center">岗位没写：{facts.filter(([v]) => !v).map(([, k]) => k).join("、")}</span>}
            </div>
            {!!detail.requirements?.length && <section><h3 className="font-semibold mb-2">任职要求</h3>
              <ul className="list-disc pl-5 text-sm space-y-1.5 marker:text-faint">{detail.requirements.map((r, i) => <li key={i}>{r}</li>)}</ul></section>}
            {!!detail.conflicts?.length && <section className="text-sm space-y-2"><h3 className="font-semibold">各处说法不一致</h3>
              {detail.conflicts.map((c, i) => <div key={i} className="rounded-lg bg-warn-soft px-3 py-2.5">
                <p>{c.field}：取了 {String(c.chosen)} · {c.rule}</p>
                {(c.alternatives ?? c.others ?? []).map((a, n) => <p key={n} className="text-muted mt-1">另一种说法：{String(a.value)}{a.sources?.length ? ` · ${a.sources.join("、")}` : ""}</p>)}
              </div>)}</section>}
            <FollowupForm key={detail.id} result={detail} changed={(followup) => {
              const next = { ...detail, followup }; setDetail(next); onChanged?.(next);
            }} />
          </> : <>
            <div className="flex flex-wrap gap-2"><Badge>{["interview", "question", "study"].includes(detail.kind) ? detail.material_label : KINDS[detail.kind]}</Badge>
              {detail.kind === "interview" && <Badge>{detail.interview_company ?? "面试公司待核实"}</Badge>}</div>
            {detail.summary && <p className="text-sm leading-relaxed">{hideRefs(detail.summary)}</p>}
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
          <p className="text-xs text-muted">{detail.posted_at ? `岗位日期 ${sourceDay(detail.posted_at)}（日期口径见来源）` : detail.kind === "job" ? "发布日期和更新时间以各来源标注为准" : ""}
            {detail.published ? `帖子发布于 ${detail.published}；实际面试日期以原文为准` : ""}
            {detail.last_seen ? ` · 最近采集于 ${detail.last_seen}` : ""}
            {detail.status === "gone" ? " · 库内标记下架，当前页面状态需核验" : ""}</p>
          {(detail.jd_text || detail.text) && <details className="text-sm"><summary className="cursor-pointer text-accent">查看采集原文</summary>
            <div className="whitespace-pre-wrap mt-3 text-muted leading-relaxed">{stripUntrusted(detail.jd_text || detail.text)}</div></details>}
        </>}
      </div>
    </div>
  </dialog>;
}

function JobCard({ r }: { r: Result }) {
  const match = r.match;
  const summary = match ? matchSummary(match) : null;
  const known = match?.checks.filter((c) => c.ok !== null) ?? [];
  const unknown = (match?.checks.length ?? 0) - known.length;
  const meta = [r.type, r.days_per_week ? `每周 ${r.days_per_week} 天` : null, r.duration_months ? `${r.duration_months} 个月` : null,
    r.salary_text].filter(Boolean);
  return <>
    <div className="flex gap-3">
      <CompanyMark name={r.company} />
      <div className="min-w-0 flex-1">
        <h3 className="font-medium text-[14.5px] leading-snug line-clamp-2">{r.title ?? r.display_title}</h3>
        <p className="text-xs text-muted mt-0.5 truncate">{[r.company, r.unit, r.city ?? "城市未说明"].filter(Boolean).join(" · ")}</p>
      </div>
      {match && <ScoreRing score={match.score} />}
    </div>
    {match ? <>
      <div className="flex flex-wrap gap-1.5">
        {summary && <CheckPill ok={summary.ok}>{summary.label}</CheckPill>}
        {known.map((c) => <CheckPill key={c.key} ok={c.ok}>{c.detail}</CheckPill>)}
        {unknown > 0 && <CheckPill ok={null}>{unknown} 项条件待确认</CheckPill>}
      </div>
      <SkillPills match={match} />
    </> : meta.length > 0 && <p className="text-xs text-muted">{meta.join(" · ")}</p>}
    {!!r.conflicting_fields?.length && <p className="text-xs text-warn">各处说法不一致：{r.conflicting_fields.join("、")}</p>}
  </>;
}

function OtherCard({ r }: { r: Result }) {
  return <>
    <div className="flex flex-wrap gap-1.5"><Badge>{KINDS[r.kind]}</Badge>
      {r.material_label && ["interview", "question", "study"].includes(r.kind) && <Badge>{r.material_label}</Badge>}</div>
    <h3 className="font-medium text-[14.5px] leading-snug line-clamp-2">{hideRefs(r.display_title)}</h3>
    <p className="text-xs text-muted">{[r.interview_company || r.company || r.companies?.join("、") || "公司待核实",
      r.role ?? r.roles?.join("、"), r.round ?? r.rounds?.join("、")].filter(Boolean).join(" · ")}</p>
    {r.summary && <p className="text-sm text-muted line-clamp-2 leading-relaxed">{hideRefs(r.summary)}</p>}
  </>;
}

// fold：聊天里用。岗位都给大卡片；面经、帖子超过 4 张先收起；面试题收成一个可点开的列表（10-12 一次回答引用了 9 道题，卡片太散）
export function ResultCards({ results, text, fold = false }: { results: Result[]; text?: string; fold?: boolean }) {
  const [opened, setOpened] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, Result>>({});
  const [more, setMore] = useState(false);
  const cards = results.map((r) => overrides[r.id] ?? r);
  const refs = useMemo(() => new Map(cards.map((r) => [r.id, r.kind === "job" ? r.display_title : hideRefs(r.display_title)])), [cards]);
  const questions = fold ? cards.filter((r) => r.kind === "question") : [];
  const rest = fold ? cards.filter((r) => r.kind !== "question") : cards;
  const others = rest.filter((r) => r.kind !== "job");
  const shown = fold && !more ? [...rest.filter((r) => r.kind === "job"), ...others.slice(0, 4)] : rest;
  const hidden = rest.length - shown.length;
  return <>
    {text && <Markdown text={text} refs={refs} onRef={setOpened} />}
    {!!shown.length && <div className={`grid gap-3 ${shown.length > 1 ? "sm:grid-cols-2" : ""} ${text ? "mt-4" : ""}`} data-testid="result-cards">
      {shown.map((r, i) => {
        const source = r.sources?.find((s) => s.url);
        const day = when(r);
        return <motion.article key={r.id} data-testid={`result-${r.id}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                               transition={{ duration: 0.25, delay: Math.min(i, 8) * 0.05 }}
                               className="flex flex-col gap-2.5 rounded-xl border border-line bg-card p-4 transition-colors hover:border-faint">
          {r.kind === "job" ? <JobCard r={r} /> : <OtherCard r={r} />}
          {(r.followup?.saved || (r.followup && r.followup.status !== "untracked")) && <div className="flex flex-wrap gap-1.5">
            {r.followup?.saved && <Badge tone="blue" icon={Bookmark}>已收藏</Badge>}
            {r.followup && r.followup.status !== "untracked" && <Badge tone="green" icon={Check}>{STATUSES[r.followup.status]}</Badge>}
          </div>}
          <div className="mt-auto flex items-center justify-between gap-3 border-t border-line pt-2.5 text-xs">
            <span className="flex items-center gap-1.5 min-w-0 text-muted">
              {source?.url ? <a href={source.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-accent">
                {source.label ?? source.source} <ExternalLink size={11} aria-hidden />
              </a> : <span>来源链接缺失</span>}
              {(r.sources?.length ?? 0) > 1 && <span className="text-faint">等 {r.sources.length} 处</span>}
              {day && <span className="text-faint truncate">· {day}</span>}
            </span>
            <button type="button" onClick={() => setOpened(r.id)} className="shrink-0 text-accent font-medium hover:underline">查看详情</button>
          </div>
        </motion.article>;
      })}
    </div>}
    {hidden > 0 && <button type="button" onClick={() => setMore(true)} className="mt-2 text-xs text-muted hover:text-foreground">
      还有 {hidden} 个面经和帖子，展开看</button>}
    {!!questions.length && <details className="mt-3 rounded-xl border border-line bg-card px-4 py-3 text-sm" data-testid="result-questions">
      <summary className="cursor-pointer select-none text-muted hover:text-foreground">回答里引用了 {questions.length} 道面试题，点开看题目和出处</summary>
      <ul className="mt-2 divide-y divide-line">
        {questions.map((q) => <li key={q.id} className="flex items-start justify-between gap-3 py-2">
          <span className="min-w-0">
            <span className="block leading-relaxed">{hideRefs(q.question ?? q.display_title)}</span>
            <span className="block text-xs text-muted">{[q.company ?? q.interview_company, q.role, q.round, q.material_label].filter(Boolean).join(" · ")}</span>
          </span>
          <button type="button" onClick={() => setOpened(q.id)} className="shrink-0 text-xs text-accent hover:underline">出处</button>
        </li>)}
      </ul>
    </details>}
    {opened && <ResultPanel key={opened} reference={opened} queryMatch={cards.find((r) => r.id === opened)?.match?.criteria
      ? cards.find((r) => r.id === opened)?.match : undefined}
      close={() => setOpened(null)} onChanged={(r) => setOverrides((old) => ({ ...old, [r.id]: r }))} />}
  </>;
}

export function FollowupList() {
  const [rows, setRows] = useState<Result[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; api<Result[]>("followups").then((r) => { if (active) setRows(r); })
    .catch((e) => { if (active) { setError((e as Error).message); setRows([]); } }); return () => { active = false; }; }, []);
  return <section className="space-y-3">
    <div>
      <h2 className="font-semibold">保存的岗位和进度</h2>
      <p className="text-sm text-muted mt-0.5">同一家公司的不同岗位分开记；收藏和投递状态也分开。</p>
    </div>
    <ErrorNote error={error} />
    {rows === null ? <div className="skeleton h-20" /> : rows.length ? <ResultCards results={rows} />
      : <p className="text-sm text-muted">在岗位详情里收藏或保存进度后，会出现在这里。</p>}
  </section>;
}
