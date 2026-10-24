"use client";

// 跟进卡：委托北极星长期盯着的求职目标。左边是卡，右边是选中那张的重点岗位、最新态势报告和变化时间线。
// 检查只读本地库、不调模型；紧急的变化会发系统通知，其余进每天的态势报告。设计见 docs/design/WATCH.md。
import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { Pause, Play, Plus, Radar, RefreshCw, ScrollText, Square } from "lucide-react";
import { Badge, Button, EmptyState, ErrorNote, Markdown, PageHeader, Skeleton } from "@/components/markdown";
import { ResultPanel } from "@/components/result-cards";
import { api } from "@/lib/client";

type Event = { id: number; kind: string; kind_label: string; importance: "urgent" | "brief" | "quiet"; ref: string | null; title: string;
  detail: { label?: string; fields?: { label: string; before: unknown; after: unknown; conflict?: boolean; added?: string[]; removed?: string[] }[] };
  created_at: string; read: boolean; version: number };
type Target = { job_id: number | null; ref: string | null; label: string; snapshot: Record<string, unknown> };
type Watch = { id: number; title: string; criteria: Record<string, unknown>; criteria_text: string; status: "active" | "paused" | "ended";
  until: string | null; cadence_minutes: number; report_hour: number; notify: "desktop" | "brief"; version: number;
  last_check_at: string | null; next_check_at: string | null; last_report_at: string | null; last_error: string | null;
  scope_active: number | null; has_unread: boolean; targets: Target[]; events: Event[];
  report: { markdown: string; fallback: string | null; period_start: string; period_end: string; created_at: string } | null };

const STATUS: Record<Watch["status"], [string, "green" | "amber" | "gray"]> = { active: ["跟进中", "green"], paused: ["已暂停", "amber"], ended: ["已结束", "gray"] };
const IMPORTANCE: Record<Event["importance"], [string, "red" | "blue" | "gray"]> = { urgent: ["紧急", "red"], brief: ["进简报", "blue"], quiet: ["安静", "gray"] };
const FALLBACK: Record<string, string> = { rule_only: "按规则生成", daily_model_budget: "今天模型额度用完，按规则生成" };

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function show(value: unknown): string {
  if (value == null || value === "") return "没写";
  if (Array.isArray(value)) return value.length ? value.join("；") : "空";
  return String(value);
}

function inTwoWeeks(): string {
  const d = new Date(Date.now() + 14 * 86400000);
  return d.toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
}

function CreateForm({ job, created }: { job: string | null; created: (w: Watch) => void }) {
  const [form, setForm] = useState({ title: "", city: "", job_type: "intern", direction: "", max_days_per_week: "", big_only: true,
    until: inTwoWeeks(), cadence_minutes: "60", report_hour: "21", notify: "desktop" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = "rounded-lg border border-line bg-card px-3 py-2 text-sm w-full outline-none focus:border-accent";
  async function submit() {
    setBusy(true); setError(null);
    try {
      const w = await api<Watch>("watches", { body: {
        title: form.title || [form.city, form.direction, "实习"].filter(Boolean).join(" "),
        criteria: { city: form.city || null, job_type: form.job_type || null, direction: form.direction || null,
          max_days_per_week: form.max_days_per_week ? Number(form.max_days_per_week) : null, big_only: form.big_only },
        job_ids: job ? [job] : [], until: form.until || null, cadence_minutes: Number(form.cadence_minutes),
        report_hour: Number(form.report_hour), notify: form.notify,
      } });
      created(w);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  return <form className="rounded-xl border border-line bg-card p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
    <h2 className="font-semibold">新建跟进卡</h2>
    {job && <p className="text-xs text-muted">会把刚才那个岗位作为重点岗位一起盯住。</p>}
    <div className="grid sm:grid-cols-3 gap-3">
      <label className="text-xs text-muted space-y-1 sm:col-span-3">名字<input aria-label="跟进卡名字" className={field} value={form.title}
        placeholder="例如：北京 Agent 日常实习（不填按条件生成）" onChange={set("title")} /></label>
      <label className="text-xs text-muted space-y-1">城市<input aria-label="城市" className={field} value={form.city} placeholder="北京" onChange={set("city")} /></label>
      <label className="text-xs text-muted space-y-1">方向<input aria-label="方向" className={field} value={form.direction} placeholder="Agent" onChange={set("direction")} /></label>
      <label className="text-xs text-muted space-y-1">类型<select aria-label="类型" className={field} value={form.job_type} onChange={set("job_type")}>
        <option value="intern">实习</option><option value="campus">校招</option><option value="social">社招</option><option value="">不限</option></select></label>
      <label className="text-xs text-muted space-y-1">每周最多几天<input aria-label="每周最多几天" type="number" min={1} max={7} className={field}
        value={form.max_days_per_week} onChange={set("max_days_per_week")} /></label>
      <label className="text-xs text-muted space-y-1">盯到哪天<input aria-label="盯到哪天" type="date" className={field} value={form.until} onChange={set("until")} /></label>
      <label className="text-xs text-muted space-y-1">多久查一次<select aria-label="检查间隔" className={field} value={form.cadence_minutes} onChange={set("cadence_minutes")}>
        <option value="30">30 分钟</option><option value="60">1 小时</option><option value="180">3 小时</option><option value="720">12 小时</option></select></label>
      <label className="text-xs text-muted space-y-1">几点写态势报告<select aria-label="报告时间" className={field} value={form.report_hour} onChange={set("report_hour")}>
        {[8, 12, 18, 21, 23].map((h) => <option key={h} value={h}>{h}:00 以后</option>)}</select></label>
      <label className="text-xs text-muted space-y-1">紧急变化<select aria-label="提醒方式" className={field} value={form.notify} onChange={set("notify")}>
        <option value="desktop">发系统通知</option><option value="brief">只进报告</option></select></label>
      <label className="text-xs text-muted flex items-center gap-2 self-end pb-2"><input type="checkbox" checked={form.big_only} onChange={set("big_only")} />只看大厂</label>
    </div>
    <div className="flex items-center gap-3">
      <Button type="submit" variant="primary" icon={Plus} disabled={busy}>{busy ? "创建中…" : "开始跟进"}</Button>
      <span className="text-xs text-muted">第一次检查只记下现在的样子，之后有变化才提醒。</span>
    </div>
    <ErrorNote error={error} />
  </form>;
}

function Detail({ watch, changed, open }: { watch: Watch; changed: (w: Watch) => void; open: (ref: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [quiet, setQuiet] = useState(false);
  // 报告正文里模型已经写了名字，编号只换成一个可点的“查看”，不再把名字重复一遍
  const refs = useMemo(() => new Map([...watch.targets.filter((t) => t.ref).map((t) => t.ref!),
    ...watch.events.filter((e) => e.ref).map((e) => e.ref!), ...(watch.report?.markdown.match(/[JPQR]-\d+/g) ?? [])].map((r) => [r, "查看"] as const)), [watch]);
  async function act(name: string, run: () => Promise<Watch>, message?: (w: Watch) => string) {
    setBusy(name); setError(null); setNote(null);
    try { const w = await run(); changed(w); if (message) setNote(message(w)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }
  const events = watch.events.filter((e) => quiet || e.importance !== "quiet");
  return <div className="space-y-5 min-w-0">
    <div className="rounded-xl border border-line bg-card p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-lg flex items-center gap-2">{watch.title}<Badge tone={STATUS[watch.status][1]}>{STATUS[watch.status][0]}</Badge></h2>
          <p className="text-sm text-muted mt-1">范围：{watch.criteria_text}{watch.scope_active != null ? ` · 现在在招 ${watch.scope_active} 个` : ""}
            {watch.until ? ` · 盯到 ${watch.until}` : ""} · 每 {watch.cadence_minutes >= 60 ? `${watch.cadence_minutes / 60} 小时` : `${watch.cadence_minutes} 分钟`}查一次</p>
          <p className="text-xs text-faint mt-1">上次检查 {when(watch.last_check_at)} · 下次 {watch.status === "active" ? when(watch.next_check_at) : "—"}
            · 上次报告 {when(watch.last_report_at)}{watch.version > 1 ? ` · 条件改过 ${watch.version - 1} 次` : ""}</p>
        </div>
        {watch.status !== "ended" && <div className="flex flex-wrap gap-2">
          <Button icon={RefreshCw} disabled={!!busy || watch.status !== "active"} onClick={() => void act("check", async () =>
            (await api<{ watch: Watch; result: { new_events: number } }>(`watches/${watch.id}/check`, { body: {} })).watch,
            (w) => `查完了：${w.events.filter((e) => !e.read).length ? "有新变化，见下面" : "没有新变化"}`)}>{busy === "check" ? "检查中…" : "立即检查"}</Button>
          <Button icon={ScrollText} disabled={!!busy || !watch.last_check_at} onClick={() => void act("report", () =>
            api<Watch>(`watches/${watch.id}/report`, { body: {} }))}>{busy === "report" ? "写报告中…" : "现在写报告"}</Button>
          {watch.status === "active"
            ? <Button icon={Pause} disabled={!!busy} onClick={() => void act("pause", () => api<Watch>(`watches/${watch.id}`, { method: "PATCH", body: { status: "paused" } }))}>暂停</Button>
            : <Button icon={Play} disabled={!!busy} onClick={() => void act("resume", () => api<Watch>(`watches/${watch.id}`, { method: "PATCH", body: { status: "active" } }))}>恢复</Button>}
          <Button icon={Square} variant="danger" disabled={!!busy} onClick={() => {
            if (window.confirm("结束后不能再打开，已有的变化和报告会留着。确定结束？"))
              void act("end", () => api<Watch>(`watches/${watch.id}`, { method: "PATCH", body: { status: "ended" } }));
          }}>结束</Button>
        </div>}
      </div>
      {note && <p className="text-xs text-ok">{note}</p>}
      {watch.last_error && <p className="text-xs text-bad">上次检查出错：{watch.last_error}</p>}
      <ErrorNote error={error} />
    </div>

    <section className="space-y-2">
      <h3 className="font-semibold">重点岗位</h3>
      {watch.targets.length ? <ul className="grid sm:grid-cols-2 gap-2">{watch.targets.map((t, i) => <li key={i} className="rounded-lg border border-line bg-card px-3 py-2.5 text-sm">
        {t.ref ? <button type="button" className="font-medium text-left hover:text-accent" onClick={() => open(t.ref!)}>{t.label}</button>
          : <span className="font-medium">{t.label}</span>}
        <p className="text-xs text-muted mt-1">{t.snapshot.removed ? "已不在库里" : t.snapshot.status === "gone" ? "已下架" : "在招"}
          {t.snapshot.days_per_week ? ` · 每周 ${t.snapshot.days_per_week} 天` : " · 天数没写"}{t.snapshot.duration_months ? ` · ${t.snapshot.duration_months} 个月` : ""}</p>
      </li>)}</ul> : <p className="text-sm text-muted">还没有重点岗位。在对话或岗位页打开一个岗位，点“加入”就会单独盯它的下架和要求变化。</p>}
    </section>

    <section className="space-y-2">
      <h3 className="font-semibold">最新态势报告</h3>
      {watch.report ? <div className="rounded-xl border border-line bg-card p-4">
        <p className="text-xs text-faint mb-2">{when(watch.report.period_start)} — {when(watch.report.period_end)}
          {watch.report.fallback ? ` · ${FALLBACK[watch.report.fallback] ?? "模型没写成，按规则生成"}` : ""}</p>
        <Markdown text={watch.report.markdown} refs={refs} onRef={open} />
      </div> : <p className="text-sm text-muted">每天 {watch.report_hour}:00 以后第一次检查时会写一份；也可以点“现在写报告”。</p>}
    </section>

    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">变化时间线</h3>
        <div className="flex items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5 text-muted"><input type="checkbox" checked={quiet} onChange={(e) => setQuiet(e.target.checked)} />显示安静的</label>
          {watch.has_unread && <button type="button" className="text-accent hover:underline" onClick={() => void act("read", async () => {
            await api(`watches/${watch.id}/events/read`, { body: {} }); return api<Watch>(`watches/${watch.id}`);
          })}>全部标为已读</button>}
        </div>
      </div>
      {events.length ? <ul className="space-y-2">{events.map((e) => <motion.li key={e.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }}
        className={`rounded-lg border px-3 py-2.5 text-sm ${e.read ? "border-line" : "border-accent/40 bg-accent-soft/30"}`}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={IMPORTANCE[e.importance][1]}>{IMPORTANCE[e.importance][0]}</Badge>
          <span className="text-xs text-muted">{e.kind_label} · {when(e.created_at)}</span>
        </div>
        <p className="mt-1.5">{e.ref ? <button type="button" className="text-left hover:text-accent" onClick={() => open(e.ref!)}>{e.title}</button> : e.title}</p>
        {!!e.detail.fields?.length && <ul className="mt-1.5 text-xs text-muted space-y-0.5">{e.detail.fields.map((f, i) => <li key={i}>
          {f.added || f.removed ? <>{f.label}：{f.added?.length ? `新增「${f.added.join("」「")}」` : ""}{f.added?.length && f.removed?.length ? "；" : ""}
            {f.removed?.length ? `去掉「${f.removed.join("」「")}」` : ""}</>
            : <>{f.label}：{show(f.before)} → <span className={f.conflict ? "text-bad" : ""}>{show(f.after)}</span>{f.conflict ? "（和你的条件冲突）" : ""}</>}
        </li>)}</ul>}
      </motion.li>)}</ul> : <p className="text-sm text-muted">{watch.last_check_at ? "还没有发现变化。" : "还没检查过。"}</p>}
    </section>
  </div>;
}

// 合成演示专用：一步步改合成资料，看跟进卡怎样发现变化。个人实例不显示。
type Script = { available: boolean; started?: boolean; step?: number; total?: number; next?: string | null };
function DemoScript({ advanced, reset }: { advanced: (watchId: number) => void; reset: () => void }) {
  const [state, setState] = useState<Script | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; api<Script>("demo/script").then((s) => { if (active) setState(s); }).catch(() => undefined);
    return () => { active = false; }; }, []);
  if (!state?.available) return null;
  async function go() {
    setBusy(true); setError(null);
    try {
      const out = await api<{ watch: number; message: string }>("demo/advance", { body: {} });
      setMessage(out.message); advanced(out.watch);
      setState(await api<Script>("demo/script"));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="rounded-xl border border-accent/40 bg-accent-soft/40 p-4 space-y-2">
    <p className="font-medium text-sm">演示剧本（合成资料）{state.started ? `：第 ${state.step} / ${state.total} 步` : ""}</p>
    <p className="text-sm text-muted">{state.next ? `下一步：${state.next}` : "剧本已经走完，可以点“现在写报告”看态势分析。"}</p>
    <div className="flex gap-2">
      {state.next && <Button variant="primary" icon={Play} disabled={busy} onClick={() => void go()}>{busy ? "推进中…" : "推进一步"}</Button>}
      {state.started && <Button icon={RefreshCw} disabled={busy} onClick={() => void (async () => {
        setBusy(true); setError(null);
        try { await api("demo/reset", { body: {} }); setMessage("剧本已重来：合成资料恢复原样，演示跟进卡已删除。"); reset(); setState(await api<Script>("demo/script")); }
        catch (e) { setError((e as Error).message); }
        finally { setBusy(false); }
      })()}>重来一遍</Button>}
    </div>
    {message && <p className="text-xs text-ok">{message}</p>}
    <ErrorNote error={error} />
  </div>;
}

export default function Watches() {
  const [rows, setRows] = useState<Watch[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [current, setCurrent] = useState<Watch | null>(null);
  const [creating, setCreating] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (focus?: number) => {
    try {
      const list = await api<Watch[]>("watches");
      setRows(list);
      const keep = list.some((w) => w.id === selected) ? selected : null;  // 选中的卡被删了（演示重来）就换成第一张
      const id = focus ?? keep ?? list[0]?.id ?? null;
      setSelected(id);
      setCurrent(id != null ? await api<Watch>(`watches/${id}`) : null);
    } catch (e) { setError((e as Error).message); setRows([]); }
  }, [selected]);
  useEffect(() => {
    // 只在打开页面时读一次地址栏参数：?id= 选中某张卡，?job= 从岗位详情过来新建并盯住这个岗位
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    const fromJob = params.get("job");
    void (async () => {
      await load(id ? Number(id) : undefined);
      if (fromJob) { setJob(fromJob); setCreating(true); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="space-y-6">
      <PageHeader icon={Radar} title="跟进" right={<Button icon={Plus} onClick={() => setCreating(!creating)}>{creating ? "收起" : "新建跟进卡"}</Button>}>
        委托北极星长期盯着一个求职目标：重点岗位下架或改了要求、范围里出现新岗位、新面经、同类求职者的新结果，都会记下来；
        紧急的发系统通知，其余每天写成一份态势报告，告诉你形势和你还差什么。检查只读本地库，采集照常由常驻采集负责。
      </PageHeader>
      <ErrorNote error={error} />
      <DemoScript advanced={(id) => void load(id)} reset={() => { setSelected(null); setCurrent(null); void load(); }} />
      {creating && <CreateForm job={job} created={(w) => { setCreating(false); setJob(null); void load(w.id); }} />}
      {rows === null ? <Skeleton className="h-40 !rounded-xl" /> : !rows.length && !creating ? (
        <div className="rounded-xl border border-line bg-card">
          <EmptyState icon={Radar} title="还没有跟进卡" action={<Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>新建一张</Button>}>
            也可以在对话里说“接下来两周帮我盯着北京的 Agent 实习”，北极星会提议开一张，你批准后生效。
          </EmptyState>
        </div>
      ) : <div className="grid lg:grid-cols-[16rem_1fr] gap-5">
        <ul className="space-y-2 min-w-0">{rows.map((w) => <li key={w.id}>
          <button type="button" onClick={() => void load(w.id)} className={`w-full text-left rounded-xl border px-3 py-2.5 transition-colors ${
            selected === w.id ? "border-accent bg-accent-soft/40" : "border-line bg-card hover:border-faint"}`}>
            <p className="font-medium text-sm flex items-center gap-1.5">{w.has_unread && <span className="size-2 rounded-full bg-accent" aria-label="有新变化" />}{w.title}</p>
            <p className="text-xs text-muted mt-0.5">{STATUS[w.status][0]} · {w.criteria_text}</p>
          </button>
        </li>)}</ul>
        {current ? <Detail key={current.id} watch={current} open={setOpened} changed={(w) => { setCurrent(w); setRows((old) => old?.map((x) => x.id === w.id ? { ...x, ...w } : x) ?? old); }} />
          : <Skeleton className="h-64 !rounded-xl" />}
      </div>}
      {opened && <ResultPanel key={opened} reference={opened} close={() => setOpened(null)} />}
    </div>
  );
}
