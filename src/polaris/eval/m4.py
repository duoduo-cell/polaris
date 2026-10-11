"""M4 指标：多来源对齐和冲突处理（自己实现的部分），检索（分块、向量、关键词、图谱、重排）和它的消融对比。

报告只放数字和方法，不放帖子原文（B 档内容只在本机）。
"""
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from polaris.config import ROOT, get_settings
from polaris.db.models import Job, LLMCall, Posting, SearchChunk
from polaris.db.session import session_scope
from polaris.eval import alignment, retrieval
from polaris.eval.m3 import fmt, pct

REPORT_DIR = ROOT / "eval" / "reports"
REPORT = REPORT_DIR / "M4.md"
REPORT_JSON = REPORT_DIR / "M4.json"


def alignment_stats() -> dict[str, Any]:
    with session_scope() as s:
        jobs = s.execute(select(Job.posting_count, Job.conflicts).where(Job.is_synthetic.is_(False))).all()
        postings = s.scalar(select(func.count()).select_from(Posting).where(Posting.is_synthetic.is_(False)))
    fields: dict[str, int] = {}
    for _, conflicts in jobs:
        for c in conflicts or []:
            fields[c["field"]] = fields.get(c["field"], 0) + 1
    sets = {name: alignment.pairwise(path) for name, path in
            (("dev", alignment.GOLD), ("holdout", alignment.HOLDOUT), ("test", alignment.TEST))}
    for v in sets.values():
        v["errors"] = [{k: e[k] for k in ("type", "a", "b", "note")} for e in v["errors"]]
    return {"postings": postings, "jobs": len(jobs), "merged": sum(n > 1 for n, _ in jobs),
            "jobs_with_conflict": sum(bool(c) for _, c in jobs), "conflict_fields": fields, "pairwise": sets,
            "conflict_detection": alignment.conflict_detection(), "provenance": alignment.provenance()}


def index_stats() -> dict[str, Any]:
    with session_scope() as s:
        kinds = dict(s.execute(select(SearchChunk.kind, func.count()).group_by(SearchChunk.kind)).all())
        embedded = s.scalar(select(func.count()).select_from(SearchChunk).where(SearchChunk.embedding.is_not(None)))
        embed = s.execute(select(func.count(), func.sum(LLMCall.prompt_tokens), func.count(LLMCall.error))
                          .where(LLMCall.purpose == "chunk_embed")).one()
    graph: dict[str, Any] = {}
    try:
        from polaris.graph.client import driver, enabled
        if enabled():
            d = driver()
            graph["nodes"] = {r["l"]: r["n"] for r in d.execute_query(
                "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l").records}
            graph["relationships"] = {r["t"]: r["n"] for r in d.execute_query(
                "MATCH ()-[e]->() RETURN type(e) AS t, count(*) AS n ORDER BY t").records}
    except Exception as e:  # noqa: BLE001
        graph["error"] = str(e)[:120]
    s_ = get_settings()
    return {"chunks": kinds, "total": sum(kinds.values()), "embedded": embedded, "embed_model": s_.embed_model,
            "embed_dim": s_.embed_dim, "embed_calls": embed[0], "embed_tokens": embed[1], "embed_errors": embed[2],
            "rerank_model": s_.rerank_model, "graph": graph}


BENCH_JSON = REPORT_DIR / "M4-bench.json"
BENCH_PRIVATE = ROOT / "data" / "eval" / "M4-bench.json"
BENCH_CASSETTES = ROOT / "data" / "eval" / "cassettes" / "m4b"
PRIVATE_TOOLS = ("posts_search", "posts_get", "questions_search")
HIDDEN = "（查过帖子或面试题，回答原文只在本机）"


def run_bench(k: int = 3) -> dict[str, Any]:
    """Polaris-Bench v2：同一套难题，knowledge 配置（加面试题检索和图谱统计）对比 search 配置（M3 的四个工具）。
    录像和完整回答只在本机（会涉及帖子原文）；公开的 JSON 里，查过帖子或面试题的回答换成占位。"""
    from polaris.agent.minimal import KNOWLEDGE, SEARCH
    from polaris.eval.runner import run_suite
    from polaris.eval.suites.bench_v2 import SUITE_VERSION, build_tasks

    tasks = build_tasks()
    full: dict[str, Any] = {"suite": SUITE_VERSION, "k": k, "profiles": {}}
    for profile in (KNOWLEDGE, SEARCH):
        cassettes = BENCH_CASSETTES / profile.name
        cassettes.mkdir(parents=True, exist_ok=True)
        for old in cassettes.glob("*.jsonl"):
            old.unlink()
        full["profiles"][profile.name] = run_suite(tasks, k=k, module="M4", suite=SUITE_VERSION, cassette_dir=cassettes,
                                                   profile=profile, purpose="m4_bench")
    BENCH_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    BENCH_PRIVATE.write_text(json.dumps(full, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    public: dict[str, Any] = {"suite": SUITE_VERSION, "k": k, "profiles": {}}
    for name, st in full["profiles"].items():
        touched = {r["task"] for r in st["results"] if any(t in PRIVATE_TOOLS for t in r["tools"])}
        public["profiles"][name] = {
            **st, "results": [{**r, "answer": HIDDEN} if r["task"] in touched else r for r in st["results"]],
            "failures": [{**f, "answer": HIDDEN} if f["task"] in touched else f for f in st["failures"]]}
    BENCH_JSON.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8", newline="\n")
    return public


def _metric(st: dict[str, Any], key: str | None) -> str:
    if key is None:
        return f"{st['replay_identical']}/{st['replay_total']}"
    v = st.get(key)
    if v is None:
        return "—"
    if key == "avg_tool_calls":
        return f"{v:.1f}"
    if key == "arg_valid_rate":
        return pct(v, 1)
    return str(v)


def render_bench(b: dict[str, Any]) -> list[str]:
    from polaris.eval.runner import CATEGORY_NAMES

    k, profiles = b["k"], b["profiles"]
    names = list(profiles)
    first = profiles[names[0]]
    lines = [f"## 5. Polaris-Bench v2：工具调用难题（{first['tasks']} 题，每题跑 {k} 次）", "",
             "比较、图谱统计、面试题、按时间筛、跨来源冲突（合成数据）、带条件的推荐（推荐了不符合条件的岗位算错）、"
             "找不到、出错恢复。标准答案建题时从库里现算（`src/polaris/eval/suites/bench_v2.py`）。"
             f"模型 `{first['model']}`，生成于 {first['generated_at']}。对比两种配置："
             "**knowledge**（6 个工具，含 questions_search、kg_stats）和 **search**（M3 的 4 个工具）。", "",
             "| 类别 | 题数 | " + " | ".join(f"{n} 单次成功 | {n} pass^{k}" for n in names) + " |",
             "|---|---|" + "---|---|" * len(names)]
    for cat in first["categories"]:
        cells = [f"{profiles[n]['categories'][cat]['success']}/{profiles[n]['categories'][cat]['runs']} | "
                 f"{profiles[n]['categories'][cat]['pass_k']}/{profiles[n]['categories'][cat]['tasks']}" for n in names]
        lines.append(f"| {CATEGORY_NAMES.get(cat, cat)} | {first['categories'][cat]['tasks']} | " + " | ".join(cells) + " |")
    totals = [f"{profiles[n]['success']}/{profiles[n]['runs']}（{pct(profiles[n]['success'], profiles[n]['runs'])}） | "
              f"**{profiles[n]['pass_k']}/{profiles[n]['tasks']}**" for n in names]
    lines.append(f"| **合计** | {first['tasks']} | " + " | ".join(totals) + " |")
    lines += ["", "| 指标 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, key in (("平均每题工具调用", "avg_tool_calls"), ("参数一次合格率（不含注入的故障）", "arg_valid_rate"),
                       ("耗时中位数（毫秒）", "latency_p50_ms"), ("耗时 P95（毫秒）", "latency_p95_ms"),
                       ("完整回放与原次一致", None)):
        lines.append(f"| {label} | " + " | ".join(_metric(profiles[n], key) for n in names) + " |")
    lines += ["", f"knowledge 各工具调用次数：{profiles[names[0]]['tool_usage']}", ""]
    for n in names:
        fails = profiles[n]["failures"]
        if not fails:
            continue
        lines += [f"{n} 没通过的：", "", "| 题 | 第几次 | 值对 | 编号对 | 工具对 | 推荐了不合条件的 |", "|---|---|---|---|---|---|"]
        for f in fails:
            lines.append(f"| {f['task']} | {f['k'] + 1} | {'是' if f['values_ok'] else '否'} | {'是' if f['cite_ok'] else '否'} | "
                         f"{'是' if f['tools_ok'] else '否'} | {'、'.join(f.get('stray') or []) or '—'} |")
        lines.append("")
    return lines


def run() -> dict[str, Any]:
    stats = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "alignment": alignment_stats(),
             "index": index_stats(), "retrieval": retrieval.evaluate()}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT.write_text(render(stats), encoding="utf-8")
    return stats


def _pair_row(name: str, desc: str, r: dict[str, Any]) -> str:
    return (f"| {name} | {desc} | {r['pairs']}（同岗 {r['positives']}） | {fmt(r['precision'])} | {fmt(r['recall'])} | "
            f"{fmt(r['f1'])} |")


def render(st: dict[str, Any]) -> str:
    a, ix, rt = st["alignment"], st["index"], st["retrieval"]
    pw = a["pairwise"]
    lines = ["# M4 指标：多来源对齐、知识图谱、混合检索", "", f"生成于 {st['generated_at']}，命令 `polaris metrics m4`。", "",
             "## 1. 多来源对齐和冲突处理（自己实现）", "",
             f"{a['postings']} 条真实挂牌对齐成 {a['jobs']} 个岗位，其中 {a['merged']} 个合并了多条（多城市、多次挂牌）；"
             f"{a['jobs_with_conflict']} 个岗位有字段说法不一致：{a['conflict_fields'] or '无'}。", "",
             "真实挂牌目前都来自各家官网（同一家只有一个来源），所以真实数据上还没有跨来源的冲突；10-11 唯一那条冲突"
             "恰好来自测试集里那对错并（\"抖音研发中台\"和\"抖音研发\"）——错并会制造假冲突，这是同岗识别宁可少并的原因。"
             "冲突处理目前只在合成数据（转载版故意改字段）上验证过，Boss 直聘的岗位接进来后才有真实的跨来源冲突。", "",
             "同岗识别的标准答案分三份，按标注时间先后，防止规则只对着看过的例子调：", "",
             "| 集合 | 怎么来的 | 对数 | 精确率 | 召回率 | F1 |", "|---|---|---|---|---|---|",
             _pair_row("开发集", "调规则时看过", pw["dev"]),
             _pair_row("留出集", "第一版规则冻结后标；暴露问题后改过规则（第一版在这份上 P=0.43 R=0.75）", pw["holdout"]),
             _pair_row("**测试集**", "**第二版规则冻结后标，只评不调**", pw["test"]), "",
             f"测试集另有 {pw['test']['unsure']} 对标注时拿不准，不计分。测试集上的错："]
    lines += [f"- {e['type']}：{e['note']}" for e in pw["test"]["errors"]] or ["- 无"]
    cd, pv = a["conflict_detection"], a["provenance"]
    lines += ["", "| 指标 | 结果 |", "|---|---|",
              f"| 冲突发现（合成数据：每个转载版故意改了一个字段） | {cd['found']}/{cd['total']} |",
              f"| 字段出处可追溯（每个字段都能追到还存在的挂牌） | {pv['traceable']}/{pv['fields']}"
              f"（{pct(pv['traceable'], pv['fields'])}） |", "",
              "标注由开发 agent 读两条挂牌的正文判断，备注写在标准答案文件里（`eval/gold/same_job_*.jsonl`），可逐条复核。", "",
              "## 2. 检索的底子", "",
              f"- 分块：{ix['total']} 块，{ix['chunks']}。岗位按职责、要求切开，帖子不到 1,500 字整篇一块，"
              "面试题单独成块，每块带上下文头。",
              f"- 向量：{ix['embed_model']}，{ix['embed_dim']} 维，{ix['embedded']}/{ix['total']} 块已算，"
              f"{ix['embed_calls']} 次调用、{ix['embed_tokens']} token、失败 {ix['embed_errors']} 次。",
              "- 关键词：ParadeDB pg_search 0.22.6 的 BM25，jieba 分词（和 lindera、icu 比过，见迁移文件说明）。",
              f"- 重排：{ix['rerank_model'] or '未配置'}。",
              f"- 知识图谱（Neo4j）：节点 {ix['graph'].get('nodes', '—')}；关系 {ix['graph'].get('relationships', '—')}。", "",
              f"## 3. 检索评测（{rt['queries']} 个问题，消融对比）", "",
              f"问题和\"想找什么\"人写（`eval/gold/retrieval_v1_queries.jsonl`）；各配置前 20 汇成候选池，评审模型按 0/1/2 打分，"
              f"共 {rt['qrels']} 条（0 分 {rt['grades']['0']}、1 分 {rt['grades']['1']}、2 分 {rt['grades']['2']}）。"
              f"开发 agent 抽查 {rt['spotcheck'].get('n', 0)} 条：三档完全一致 {rt['spotcheck'].get('exact', 0)} 条，"
              f"相关 / 不相关一致 {rt['spotcheck'].get('binary', 0)} 条；不一致里评审偏宽 {rt['spotcheck'].get('judge_higher', 0)} 条、"
              f"偏严 {rt['spotcheck'].get('judge_lower', 0)} 条。", "",
              "| 配置 | NDCG@10 | MRR | MAP | P@10 | Recall@10 | 近重复率 | 前 10 有评审 | 耗时中位数 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, m in rt["table"].items():
        lines.append(f"| {name} | {m['ndcg']:.3f} | {m['mrr']:.3f} | {m['map']:.3f} | {m['p']:.3f} | {m['recall']:.3f} | "
                     f"{m['dup']:.1%} | {m['judged']:.0%} | {m['p50_ms']} ms |")
    scopes = {"jobs": "岗位", "posts": "帖子", "questions": "面试题", "all": "混合"}
    names = list(rt["table"])
    lines += ["", "按问题类型的 NDCG@10：", "", "| 配置 | " + " | ".join(scopes[s] for s in rt["ndcg_by_scope"]) + " |",
              "|---|" + "---|" * len(rt["ndcg_by_scope"])]
    for name in names:
        lines.append(f"| {name} | " + " | ".join(f"{rt['ndcg_by_scope'][s][name]:.3f}" for s in rt["ndcg_by_scope"]) + " |")
    lines += ["", "Recall@10 低是因为每个问题相关文档多（中位数三十多条），前 10 装不下；看 NDCG 和 P@10。", "",
              "## 4. 已知的不足", "",
              "- 同岗识别：多语言 JD（同一岗位日文版、英文版）会被分开（漏并）；部门名只是字面包含、正文又相近的会被并"
              "（测试集里\"抖音研发中台\"和\"抖音研发\"）。下一轮改规则后要再标一份新测试集。",
              "- 检索评测：候选池来自被评的这几种配置（pooling 的固有偏差）；评审偏宽，会把\"同方向但类型不对\"给 1 分；"
              "\"混合\"类只有 2 个问题，不下结论。",
              "- 重排阈值：qwen3-rerank 的分数三档重叠很多（不相关中位数 0.49、部分相关 0.65、完全相关 0.84），"
              "阈值 0.35 只能修掉尾巴，而且是在评测用的同一批数据上选的。",
              "- 帖子和岗位的关联很保守（团队名要能对上部门名），目前只连上少数几条；多数帖子只连到公司、技能和方向。", ""]
    if BENCH_JSON.exists():
        lines += render_bench(json.loads(BENCH_JSON.read_text(encoding="utf-8")))
    return "\n".join(lines)
