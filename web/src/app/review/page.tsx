"use client";

// 竞争力：按 HR 的眼光看你在一个范围里的情况（资格、技能需求、经历证据、还缺的信息），
// 再和同类求职者的公开自述逐项对照（三层可比程度，单列拿到 offer 的人）。全部由规则算，不调模型。
import { useState } from "react";
import { Scale, Search } from "lucide-react";
import { Badge, Button, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/markdown";
import { ResultPanel } from "@/components/result-cards";
import { tool } from "@/lib/client";

type Skill = { skill: string; jobs: number; share: number | null; in_your_card: boolean };
type Review = {
  scope: string;
  qualification: { summary: string; hard_gates: Record<string, number>; gate_examples: Record<string, string[]>;
    unknown_checks: Record<string, number>; no_conflict_examples: string[] };
  evidence: { skills_demand: Skill[]; your_skills: string[]; internships: string[]; papers: string[];
    projects: { text: string; github: { ok: boolean; repo: string; url?: string; language?: string; pushed_at?: string; stars?: number;
      has_readme?: boolean; has_tests?: boolean; error?: string } | null }[] };
  you: Record<string, string[]>;
  ask_user: { field: string; why: string }[];
};
type Stats = { people: number; outcome: Record<string, number>; stage: Record<string, number>; school: Record<string, number>;
  papers_stated: number; top_papers: number; internships_known: number; internships_median: number | null };
type Compare = {
  you: { stage: string | null; school: string | null; school_bucket: string; papers: string | null; internships: string | null };
  layers: { layer: string; all: Stats; offer: Stats; enough: boolean; examples: { ref: string; stage: string; school_bucket: string; outcome: string; published: string | null }[] }[];
  reading: string[]; note: string;
};

const fmt = (o: Record<string, number>) => Object.entries(o).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join("、") || "—";

function Refs({ refs, open }: { refs: string[]; open: (r: string) => void }) {
  return <>{refs.map((r, i) => <button key={r} type="button" className="text-accent hover:underline" onClick={() => open(r)}>
    {i ? "、" : ""}例 {i + 1}</button>)}</>;
}

export default function ReviewPage() {
  const [form, setForm] = useState({ city: "", job_type: "intern", direction: "", max_days_per_week: "", company: "", big_only: true });
  const [review, setReview] = useState<Review | null>(null);
  const [compare, setCompare] = useState<Compare | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const field = "rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-faint";
  async function run() {
    setBusy(true); setError(null);
    const scope = { city: form.city || null, job_type: form.job_type || null, direction: form.direction || null,
      max_days_per_week: form.max_days_per_week ? Number(form.max_days_per_week) : null, big_only: form.big_only };
    try {
      const [r, c] = await Promise.all([
        tool<Review>("profile_review", scope),
        form.direction ? tool<Compare>("peers_compare", { direction: form.direction, company: form.company || null,
          job_type: { intern: "实习", campus: "校招", social: "社招" }[form.job_type] ?? null }) : Promise.resolve(null),
      ]);
      setReview(r); setCompare(c);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  const q = review?.qualification;
  return (
    <div className="space-y-6">
      <PageHeader icon={Scale} title="竞争力">
        先按 HR 的三个问题看你：资格上被哪些硬门槛挡住；岗位常要求的技能你的卡片对得上多少；经历有没有证据、还缺哪些信息。
        再和同类求职者在帖子里公开自述的背景逐项对照。样本是网上发帖的人，不代表录用标准；“没写”不等于没有。
      </PageHeader>
      <form className="flex flex-wrap gap-2 items-center" onSubmit={(e) => { e.preventDefault(); void run(); }}>
        <input aria-label="城市" className={`${field} w-24`} placeholder="城市" value={form.city} onChange={set("city")} />
        <input aria-label="方向" className={`${field} w-28`} placeholder="方向，如 Agent" value={form.direction} onChange={set("direction")} />
        <select aria-label="类型" className={field} value={form.job_type} onChange={set("job_type")}>
          <option value="intern">实习</option><option value="campus">校招</option><option value="social">社招</option><option value="">不限</option></select>
        <input aria-label="每周最多几天" type="number" min={1} max={7} className={`${field} w-32`} placeholder="每周最多几天"
               value={form.max_days_per_week} onChange={set("max_days_per_week")} />
        <input aria-label="对照的公司" className={`${field} w-28`} placeholder="对照公司（可空）" value={form.company} onChange={set("company")} />
        <label className="text-xs text-muted flex items-center gap-1.5"><input type="checkbox" checked={form.big_only} onChange={set("big_only")} />只看大厂</label>
        <Button type="submit" variant="primary" icon={Search} disabled={busy}>{busy ? "计算中…" : "看看"}</Button>
      </form>
      <ErrorNote error={error} />
      {busy && !review && <Skeleton className="h-64 !rounded-xl" />}
      {!review && !busy && <div className="rounded-xl border border-line bg-card">
        <EmptyState icon={Scale} title="填上你这次想找的范围">方向必填才会做同类对照。结果用的是你的记忆卡片；卡片没写的会列在“要补充的信息”里。</EmptyState>
      </div>}
      {review && q && <>
        <section className="rounded-xl border border-line bg-card p-4 space-y-3" aria-label="资格">
          <h2 className="font-semibold">1. 资格：{review.scope}</h2>
          <p className="text-sm">{q.summary}{q.no_conflict_examples.length ? <span className="text-muted">（<Refs refs={q.no_conflict_examples} open={setOpened} />）</span> : null}</p>
          {Object.keys(q.hard_gates).length ? <ul className="space-y-1.5 text-sm">{Object.entries(q.hard_gates).map(([k, n]) => <li key={k} className="flex flex-wrap gap-2 items-center">
            <Badge tone="red">{k}</Badge><span>{n} 个岗位因为这条和你的条件冲突</span>
            <span className="text-xs text-muted"><Refs refs={q.gate_examples[k] ?? []} open={setOpened} /></span></li>)}</ul>
            : <p className="text-sm text-muted">范围里没有发现和你记录的条件明确冲突的岗位。</p>}
          {Object.keys(q.unknown_checks).length > 0 && <p className="text-xs text-muted">岗位没写清、要问 HR 的：{fmt(q.unknown_checks)}</p>}
        </section>

        <section className="rounded-xl border border-line bg-card p-4 space-y-3" aria-label="技能需求">
          <h2 className="font-semibold">2. 证据：岗位常要求什么，你的卡片里有没有</h2>
          {review.evidence.skills_demand.length ? <table className="w-full text-sm"><thead><tr className="text-xs text-muted text-left">
            <th className="font-normal pb-2">技能</th><th className="font-normal pb-2 text-right">要求它的岗位</th><th className="font-normal pb-2 text-right">你的卡片</th></tr></thead>
            <tbody>{review.evidence.skills_demand.map((s) => <tr key={s.skill} className="border-t border-line">
              <td className="py-1.5">{s.skill}</td><td className="text-right tabular-nums">{s.jobs}{s.share != null ? `（${Math.round(s.share * 100)}%）` : ""}</td>
              <td className="text-right">{s.in_your_card ? <span className="text-ok">有</span> : <span className="text-muted">没写</span>}</td></tr>)}</tbody></table>
            : <p className="text-sm text-muted">范围里的岗位还没抽出技能要求。</p>}
          <div className="text-sm space-y-1.5">
            <p><span className="text-muted">项目：</span>{review.evidence.projects.length ? "" : "卡片里没写"}</p>
            {review.evidence.projects.map((p, i) => <div key={i} className="rounded-lg bg-soft px-3 py-2">
              <p>{p.text}</p>
              {p.github && <p className="text-xs text-muted mt-1">{p.github.ok ? <>GitHub {p.github.repo}：{p.github.language ?? "语言未知"}
                {p.github.pushed_at ? ` · 最近提交 ${p.github.pushed_at.slice(0, 10)}` : ""} · {p.github.has_readme ? "有 README" : "没有 README"}
                · {p.github.has_tests ? "有测试目录" : "没看到测试目录"}</> : `GitHub ${p.github.repo}：${p.github.error}`}</p>}
            </div>)}
            <p><span className="text-muted">实习：</span>{review.evidence.internships.join("；") || "卡片里没写"}</p>
            <p><span className="text-muted">论文：</span>{review.evidence.papers.join("；") || "卡片里没写"}</p>
          </div>
        </section>

        {review.ask_user.length > 0 && <section className="rounded-xl border border-warn/40 bg-warn-soft p-4 space-y-2" aria-label="要补充的信息">
          <h2 className="font-semibold">3. 可追问：卡片里还缺这些，HR 和面试官多半会问</h2>
          <ul className="text-sm list-disc pl-5 space-y-1">{review.ask_user.map((a) => <li key={a.field}>{a.why}</li>)}</ul>
          <p className="text-xs text-muted">在对话里直接告诉北极星就会记进卡片。</p>
        </section>}
      </>}

      {compare && <section className="rounded-xl border border-line bg-card p-4 space-y-4" aria-label="同类对照">
        <h2 className="font-semibold">4. 和同类求职者对照（近半年公开自述）</h2>
        <p className="text-sm text-muted">你：{compare.you.stage ?? "学历没写"} · 学校{compare.you.school ? `${compare.you.school}（${compare.you.school_bucket.replace("写了 ", "")}）` : "没写"}
          · 论文{compare.you.papers ?? "没写"} · 实习{compare.you.internships ?? "没写"}</p>
        {compare.layers.map((l) => <div key={l.layer} className="space-y-2">
          <h3 className="font-medium text-sm flex items-center gap-2">{l.layer}<Badge tone={l.enough ? "blue" : "amber"}>{l.all.people} 人{l.enough ? "" : "，样本少"}</Badge></h3>
          <table className="w-full text-sm"><thead><tr className="text-xs text-muted text-left"><th className="font-normal pb-1.5 w-24"></th>
            <th className="font-normal pb-1.5">全部 {l.all.people} 人</th><th className="font-normal pb-1.5">拿到 offer 的 {l.offer.people} 人</th></tr></thead>
            <tbody>{([["结果", (s: Stats) => fmt(s.outcome)], ["学历", (s: Stats) => fmt(s.stage)], ["学校", (s: Stats) => fmt(s.school)],
              ["论文", (s: Stats) => `写了 ${s.papers_stated} 人${s.top_papers ? `，顶会/CCF-A ${s.top_papers} 人` : ""}`],
              ["实习", (s: Stats) => `段数已知 ${s.internships_known} 人${s.internships_median != null ? `，中位数 ${s.internships_median} 段` : ""}`]] as const)
              .map(([name, f]) => <tr key={name} className="border-t border-line align-top"><td className="py-1.5 text-muted">{name}</td>
                <td className="py-1.5">{f(l.all)}</td><td className="py-1.5">{f(l.offer)}</td></tr>)}</tbody></table>
          {l.examples.length > 0 && <p className="text-xs text-muted">原帖：<Refs refs={l.examples.map((e) => e.ref)} open={setOpened} /></p>}
        </div>)}
        <ul className="text-sm list-disc pl-5 space-y-1">{compare.reading.map((r, i) => <li key={i}>{r}</li>)}</ul>
        <p className="text-xs text-faint">{compare.note}</p>
      </section>}
      {opened && <ResultPanel key={opened} reference={opened} close={() => setOpened(null)} />}
    </div>
  );
}
