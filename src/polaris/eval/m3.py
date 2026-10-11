"""M3 指标：采集（覆盖、重复、大厂占比、限流）和抽取（逐字段对标准答案）。

标准答案：
- 真实岗位：eval/gold/jobs_v1.jsonl（腾讯 20、字节 20、小红书官网 10，只存标签不存原文，进仓库）；
- 合成岗位：eval/fixtures/synth_jobs.jsonl 里的 truth（只用核对通过的）；
- 小红书笔记：data/gold/ 下的开发集和留出集（B 档，只在本机，不进仓库；报告里只放汇总数字）。
  开发集的错例被用来改过提示词，分数会偏高；留出集是改提示词之后才采、才标的，用来看改动是否真的泛化。
结果写到 eval/reports/M3.md 和 M3.json。不调模型，只读数据库。

工具调用套件 real-v1（`polaris metrics m3-tools`，会调模型）单独跑，结果写到 M3-tools.json，M3.md 里带上汇总。
录像里凡是查过小红书笔记的（工具输出里有笔记内容，B 档），连同回答原文一起只存在本机 data/ 下。
"""
import json
import re
import shutil
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from polaris.config import ROOT
from polaris.db.models import LLMCall, Post, Posting, RawItem, Run, SearchQuery
from polaris.db.session import session_scope
from polaris.eval.scoring import NORMALIZERS, POST_NORMALIZERS, FieldScore, norm_title, score, summary
from polaris.extract import JOB_PROMPT_VERSION, POST_PROMPT_VERSION
from polaris.runs import git_commit
from polaris.synth.jobs import read_fixture

REPORT_DIR = ROOT / "eval" / "reports"
JOB_GOLD = ROOT / "eval" / "gold" / "jobs_v1.jsonl"
TOOLS_JSON = REPORT_DIR / "M3-tools.json"
PUBLIC_CASSETTES = ROOT / "eval" / "cassettes" / "m3"
PRIVATE_CASSETTES = ROOT / "data" / "cassettes" / "m3"
PRIVATE_REPORT = ROOT / "data" / "reports" / "M3-tools.full.json"
POST_GOLD = ROOT / "data" / "gold" / "xhs_posts_v1.jsonl"
POST_GOLD_SETS = ((POST_GOLD, "小红书笔记·开发集（据它的错例改过提示词）"),
                  (ROOT / "data" / "gold" / "xhs_posts_holdout_v1.jsonl", "小红书笔记·留出集（改提示词时没见过）"))
SOURCE_NAMES = {"tencent:careers": "腾讯招聘官网", "bytedance:campus": "字节校招官网", "xhs:careers": "小红书招聘官网",
                "xhs": "小红书笔记", "boss": "Boss直聘"}
COLLECT_SOURCE = {"tencent": "tencent:careers", "bytedance": "bytedance:campus", "xhs_careers": "xhs:careers",
                  "xhs": "xhs", "boss": "boss"}
FIELD_NAMES = {"company": "公司", "title": "岗位名", "city": "城市", "salary_min": "薪资下限", "salary_max": "薪资上限",
               "salary_unit": "薪资单位", "job_type": "类型", "can_convert": "能否转正", "posted_at": "发布日期",
               "deadline": "截止日", "duration_months": "实习月数", "days_per_week": "每周天数",
               "post_type": "帖子类型", "big_companies": "涉及大厂", "has_referral": "能内推",
               "interview_rounds": "面试轮数"}


def pct(n: float, d: float) -> str:
    return f"{n / d:.0%}" if d else "—"


def fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------- 采集 ----------------

def _shingles(text: str, n: int = 3) -> set[str]:
    t = re.sub(r"\s+|#[^#\s]*\[话题\]#|\[[^\]]{1,6}R\]", "", text)[:400]
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def near_duplicates(texts: dict[int, str], threshold: float = 0.6) -> list[tuple[int, int]]:
    """编号不同、内容几乎一样（字符三元组 Jaccard ≥ 阈值）的成对条目。小红书上常见同一条招聘被多人搬运。"""
    sh = {k: _shingles(v) for k, v in texts.items()}
    keys, pairs = sorted(sh), []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            union = sh[a] | sh[b]
            if union and len(sh[a] & sh[b]) / len(union) >= threshold:
                pairs.append((a, b))
    return pairs


def collection_stats() -> dict[str, Any]:
    with session_scope() as s:
        per_source = dict(s.execute(select(RawItem.source, func.count()).where(RawItem.is_synthetic.is_(False))
                                    .group_by(RawItem.source)).all())
        runs = s.execute(select(Run.params, Run.stats, Run.status).where(Run.kind == "collect")).all()
        queries = s.execute(select(SearchQuery.source, func.count(), func.count(SearchQuery.last_run_at))
                            .group_by(SearchQuery.source)).all()
        postings = s.execute(select(RawItem.source, Posting.big_company, Posting.company_name, Posting.title,
                                    Posting.city).join(RawItem, RawItem.id == Posting.raw_item_id)
                             .where(RawItem.is_synthetic.is_(False))).all()
        posts = s.execute(select(Post.big_companies).join(RawItem, RawItem.id == Post.raw_item_id)).scalars().all()
        xhs_texts = dict(s.execute(select(RawItem.id, RawItem.raw_text).where(RawItem.source == "xhs")).all())

    out: dict[str, dict[str, Any]] = {src: {"items": n} for src, n in per_source.items()}
    for params, stats, status in runs:
        src = COLLECT_SOURCE.get((params or {}).get("source"), (params or {}).get("source"))
        d = out.setdefault(src, {"items": 0})
        stats = stats or {}
        d["runs"] = d.get("runs", 0) + 1
        d["runs_failed"] = d.get("runs_failed", 0) + (status != "ok")
        for k in ("items", "inserted", "failures", "rate_limited", "calls", "seconds", "feeds_seen", "opened",
                  "skipped_known", "skipped_rule", "cards_seen", "skipped_small"):
            if k in stats:
                key = "seen" if k == "items" else k
                d[key] = d.get(key, 0) + (stats[k] or 0)
    for src, total, searched in queries:
        d = out.setdefault(COLLECT_SOURCE.get(src, src), {"items": 0})
        d["queries_total"], d["queries_searched"] = total, searched

    by_src: dict[str, list[Any]] = defaultdict(list)
    for row in postings:
        by_src[row.source].append(row)
    for src, rows in by_src.items():
        keys = Counter((r.big_company or r.company_name, norm_title(r.title), r.city) for r in rows)
        out[src].update({"extracted": len(rows), "big": sum(1 for r in rows if r.big_company),
                         "dup_groups": sum(1 for c in keys.values() if c > 1),
                         "dup_items": sum(c - 1 for c in keys.values() if c > 1)})
    if "xhs" in out:
        pairs = near_duplicates(xhs_texts)
        out["xhs"].update({"extracted": len(posts), "big": sum(1 for b in posts if b),
                           "dup_items": len({b for _, b in pairs}), "dup_pairs": len(pairs)})
    return out


# ---------------- 抽取 ----------------

def _postings_by_key(synthetic: bool) -> dict[tuple[str, str], dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(RawItem.source, RawItem.external_id, Posting)
                         .join(Posting, Posting.raw_item_id == RawItem.id)
                         .where(RawItem.is_synthetic.is_(synthetic)).order_by(Posting.id)).all()
        return {(src, ext): {f: getattr(p, f if f != "company" else "company_name") for f in NORMALIZERS}
                for src, ext, p in rows}


def _posts_by_key() -> dict[tuple[str, str], dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(RawItem.source, RawItem.external_id, Post)
                         .join(Post, Post.raw_item_id == RawItem.id).order_by(Post.id)).all()
        return {(src, ext): {f: getattr(p, f) for f in POST_NORMALIZERS} for src, ext, p in rows}


def _score_set(name: str, gold: list[dict[str, Any]], preds: dict[tuple[str, str], dict[str, Any]],
               normalizers: dict[str, Any], keep_examples: bool = True) -> dict[str, Any]:
    pairs = [(f"{g['source']}#{g['external_id']}", g, preds.get((g["source"], g["external_id"]))) for g in gold]
    scores = score(pairs, normalizers, max_examples=3 if keep_examples else 0)
    return {"name": name, "n": len(gold), "private": not keep_examples,
            "extracted": sum(1 for _, _, p in pairs if p is not None),
            "summary": summary(scores), "fields": {f: _field(s) for f, s in scores.items()}}


def _field(s: FieldScore) -> dict[str, Any]:
    return {"n": s.n, "accuracy": s.accuracy, "f1": s.f1, "tp": s.tp, "fp": s.fp, "fn": s.fn,
            "hallucinated": s.hallucinated, "examples": s.examples}


def extraction_stats() -> list[dict[str, Any]]:
    sets = []
    real = read_jsonl(JOB_GOLD)
    if real:
        sets.append(_score_set("真实岗位（腾讯、字节、小红书官网）", real, _postings_by_key(False), NORMALIZERS))
    synth = [{"source": r["source"], "external_id": r["id"], **r["truth"]} for r in read_fixture()
             if r["meta"]["checked"] and r["raw_text"]]
    if synth:
        sets.append(_score_set("合成岗位", synth, _postings_by_key(True), NORMALIZERS))
    for path, name in POST_GOLD_SETS:
        posts = read_jsonl(path)
        if posts:
            sets.append(_score_set(name + "，B 档答案只在本机", posts, _posts_by_key(), POST_NORMALIZERS,
                                   keep_examples=False))
    return sets


def llm_stats() -> dict[str, Any]:
    out = {}
    with session_scope() as s:
        for purpose, version in (("extract_job", JOB_PROMPT_VERSION), ("extract_post", POST_PROMPT_VERSION)):
            rows = s.execute(select(LLMCall.latency_ms, LLMCall.parsed_ok, LLMCall.prompt_tokens,
                                    LLMCall.completion_tokens, LLMCall.reasoning_tokens, LLMCall.cost_usd,
                                    LLMCall.attempt)
                             .where(LLMCall.purpose == purpose, LLMCall.prompt_version == version,
                                    LLMCall.cache_hit.is_(False))).all()
            if not rows:
                continue
            lat = sorted(r.latency_ms for r in rows)
            out[purpose] = {
                "calls": len(rows), "parsed_ok": sum(1 for r in rows if r.parsed_ok),
                "retries": sum(1 for r in rows if (r.attempt or 1) > 1),
                "latency_p50_ms": int(statistics.median(lat)), "latency_p95_ms": lat[int(0.95 * (len(lat) - 1))],
                "prompt_tokens_avg": int(statistics.mean(r.prompt_tokens or 0 for r in rows)),
                "completion_tokens_avg": int(statistics.mean(r.completion_tokens or 0 for r in rows)),
                "reasoning_tokens_avg": int(statistics.mean(r.reasoning_tokens or 0 for r in rows)),
                "cost_usd": str(sum((r.cost_usd for r in rows if r.cost_usd is not None), start=0)) or None,
            }
    return out


# ---------------- 工具调用套件 real-v1 ----------------

def _touches_posts(path: Path) -> bool:
    return any('"tool": "posts_' in line for line in path.read_text(encoding="utf-8").splitlines())


def run_tools(k: int = 3) -> dict[str, Any]:
    from polaris.agent.minimal import SEARCH
    from polaris.eval.runner import run_suite
    from polaris.eval.suites.real_v1 import SUITE_VERSION, build_tasks

    for d in (PUBLIC_CASSETTES, PRIVATE_CASSETTES):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.jsonl"):
            old.unlink()
    stats = run_suite(build_tasks(), k=k, module="M3", suite=SUITE_VERSION, cassette_dir=PUBLIC_CASSETTES,
                      profile=SEARCH, purpose="m3_tool_calling")
    private = sorted(c.stem for c in PUBLIC_CASSETTES.glob("*.jsonl") if _touches_posts(c))
    for name in private:  # 查过笔记的录像只留在本机
        shutil.move(PUBLIC_CASSETTES / f"{name}.jsonl", PRIVATE_CASSETTES / f"{name}.jsonl")
    PRIVATE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_REPORT.write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    touched = {r["task"] for r in stats["results"] if any(t.startswith("posts_") for t in r["tools"])}
    public = {**stats, "private_tasks": sorted(touched), "private_cassettes": private,
              "results": [{**r, "answer": "（涉及小红书笔记，原文只在本机）"} if r["task"] in touched else r
                          for r in stats["results"]],
              "failures": [{**f, "answer": "（涉及小红书笔记，原文只在本机）"} if f["task"] in touched else f
                           for f in stats["failures"]]}
    TOOLS_JSON.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
                          newline="\n")
    return public


def render_tools(t: dict[str, Any]) -> list[str]:
    from polaris.eval.runner import CATEGORY_NAMES

    k = t["k"]
    lines = [f"## 工具调用套件 real-v1（{t['tasks']} 题，每题跑 {k} 次）", "",
             f"真实数据（大厂官网岗位 + 小红书笔记），工具 jobs_search / jobs_get / posts_search / posts_get，提示词 "
             f"`{t['prompt_version']}`，模型 `{t['model']}`，生成于 {t['generated_at']}。按最终结果评分；"
             f"pass^{k} 指同一题 {k} 次全部成功。", "",
             f"| 类别 | 题数 | 单次成功率 | pass^{k} |", "|---|---|---|---|"]
    for cat, c in t["categories"].items():
        lines.append(f"| {CATEGORY_NAMES.get(cat, cat)} | {c['tasks']} | {c['success']}/{c['runs']}"
                     f"（{pct(c['success'], c['runs'])}） | {c['pass_k']}/{c['tasks']} |")
    lines += [f"| **合计** | {t['tasks']} | {t['success']}/{t['runs']}（{pct(t['success'], t['runs'])}） | "
              f"**{t['pass_k']}/{t['tasks']}（{pct(t['pass_k'], t['tasks'])}）** |", "",
              "| 指标 | 结果 |", "|---|---|",
              f"| 工具调用（含注入的故障） | {t['tool_calls']} 次，各工具 {t.get('tool_usage', {})} |",
              f"| 参数一次合格率（不含注入的故障） | {pct(t['arg_valid_rate'] or 0, 1) if t['arg_valid_rate'] is not None else '—'} |",
              f"| 注入的故障 | {t['injected']} 次 |",
              f"| 每题耗时中位数 / P95 | {t['latency_p50_ms']} ms / {t['latency_p95_ms']} ms |",
              f"| 模型调用追踪完整率 | {t['llm_trace_complete']}/{t['llm_answered']} |",
              f"| 完整回放与原次回答一致 | {t['replay_identical']}/{t['replay_total']} |", ""]
    if t["failures"]:
        lines += ["没通过的：", "", "| 题 | 第几次 | 值对 | 编号对 | 工具对 | 回答开头 |", "|---|---|---|---|---|---|"]
        for f in t["failures"]:
            ans = (f["error"] or f["answer"]).replace("\n", " ").replace("|", "／")[:80]
            lines.append(f"| {f['task']} | {f['k'] + 1} | {'是' if f['values_ok'] else '否'} | "
                         f"{'是' if f['cite_ok'] else '否'} | {'是' if f['tools_ok'] else '否'} | {ans} |")
        lines.append("")
    lines += [f"录像：只查官网岗位的在 `eval/cassettes/m3/`；查过笔记的 {len(t.get('private_cassettes', []))} 个只在本机。", ""]
    return lines


# ---------------- 报告 ----------------

def render(stats: dict[str, Any]) -> str:
    c = stats["collection"]
    lines = ["# M3 指标：采集和抽取", "",
             f"生成时间 {stats['generated_at']}，代码版本 `{stats['git_commit']}`。抽取用开发期模型，提示词版本 "
             f"`{JOB_PROMPT_VERSION}` / `{POST_PROMPT_VERSION}`。", "",
             "## 采集", "",
             "| 来源 | 库里条数 | 采集轮数（失败） | 这些轮看到 / 新写入 | 再次看到的比例 | 抽取后属于大厂 | 疑似重复 "
             "| 查询词轮到 | 失败 / 限流 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for src in ("tencent:careers", "bytedance:campus", "xhs:careers", "xhs", "boss"):
        d = c.get(src)
        if not d:
            lines.append(f"| {SOURCE_NAMES[src]} | 还没采 | | | | | | | |")
            continue
        seen, ins = d.get("seen", 0), d.get("inserted", 0)
        lines.append(
            f"| {SOURCE_NAMES[src]} | {d.get('items', 0)} | {d.get('runs', 0)}（{d.get('runs_failed', 0)}） | "
            f"{seen} / {ins} | {pct(seen - ins, seen)} | "
            f"{d.get('big', 0)}/{d.get('extracted', 0)}（{pct(d.get('big', 0), d.get('extracted', 0))}） | "
            f"{d.get('dup_items', 0)} | {d.get('queries_searched', 0)}/{d.get('queries_total', 0)} | "
            f"{d.get('failures', 0)} / {d.get('rate_limited', 0)} |")
    x = c.get("xhs", {})
    lines += ["",
              "- 再次看到的比例：采集时遇到已经在库里的条目（按来源加编号、或内容指纹判断）不再写入。",
              "- 疑似重复：岗位按 公司+岗位名+城市 相同算；小红书笔记按正文字符三元组相似度 ≥ 0.6 算（同一条招聘被不同人搬运）。",
              "- 官网是公开接口或页面，没有限流；小红书一次工具调用要几十秒，按轮慢慢采。"]
    if x.get("calls"):
        lines.append(f"- 小红书：本阶段共调用 {x['calls']} 次，平均每次 {x.get('seconds', 0) / x['calls']:.0f} 秒；"
                     f"看到 {x.get('feeds_seen', 0)} 条搜索结果，按规则跳过 {x.get('skipped_rule', 0)} 条、"
                     f"采过的跳过 {x.get('skipped_known', 0)} 条，打开正文 {x.get('opened', 0)} 条。")
    b = c.get("boss", {})
    if b.get("cards_seen"):
        lines.append(f"- Boss：看到 {b['cards_seen']} 个岗位卡片，名单外的公司跳过 {b.get('skipped_small', 0)} 个。")

    lines += ["", "## 抽取（逐字段对标准答案）", "",
              "准确率：两边都空也算对。F1：只看有值的格子，多填（幻觉）和漏填都扣分；答案全空的字段算不了 F1，看多填次数。", ""]
    for es in stats["extraction"]:
        sm = es["summary"]
        lines += [f"### {es['name']}（{es['n']} 条，抽出 {es['extracted']} 条）", "",
                  f"总体：微平均 F1 **{fmt(sm['micro_f1'])}**，宏平均 F1 {fmt(sm['macro_f1'])}，"
                  f"格子准确率 {fmt(sm['accuracy'])}，多填 {sm['hallucinated']} 处。", "",
                  "| 字段 | 准确率 | F1 | 对 / 多填或填错 / 漏填 | 答案为空却填了 |", "|---|---|---|---|---|"]
        for f, d in es["fields"].items():
            lines.append(f"| {FIELD_NAMES.get(f, f)} | {fmt(d['accuracy'])} | {fmt(d['f1'])} | "
                         f"{d['tp']} / {d['fp']} / {d['fn']} | {d['hallucinated']} |")
        wrong = [(f, e) for f, d in es["fields"].items() for e in d["examples"]]
        if wrong:
            lines += ["", "<details><summary>错例（每个字段最多 3 条）</summary>", "", "| 字段 | 条目 | 答案 | 模型 |",
                      "|---|---|---|---|"]
            lines += [f"| {FIELD_NAMES.get(f, f)} | {e['id']} | {e['gold']} | {e['pred']} |" for f, e in wrong]
            lines += ["", "</details>"]
        lines.append("")

    tools = json.loads(TOOLS_JSON.read_text(encoding="utf-8")) if TOOLS_JSON.exists() else None
    lines += render_tools(tools) if tools else ["## 工具调用套件 real-v1", "", "还没跑：`uv run polaris metrics m3-tools`。", ""]

    lines += ["## 抽取的调用成本", "", "| 用途 | 调用 | 结构化通过 | 重试 | 延迟中位数 / P95 | 平均 token（输入 / 输出 / 其中思考） |",
              "|---|---|---|---|---|---|"]
    for purpose, d in stats["llm"].items():
        lines.append(f"| {purpose} | {d['calls']} | {pct(d['parsed_ok'], d['calls'])} | {d['retries']} | "
                     f"{d['latency_p50_ms']} ms / {d['latency_p95_ms']} ms | {d['prompt_tokens_avg']} / "
                     f"{d['completion_tokens_avg']} / {d['reasoning_tokens_avg']} |")
    lines += ["", "开发期模型的思考关不掉，输出 token 里大半是思考。", "",
              "复现：`uv run polaris collect tencent|bytedance|xhs-careers|xhs|boss`、`uv run polaris extract job|post`、"
              "`uv run polaris metrics m3`。小红书和 Boss 需要本机登录，答案集只在作者本机。", ""]
    return "\n".join(lines)


def run() -> dict[str, Any]:
    stats = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_commit": git_commit(),
             "collection": collection_stats(), "extraction": extraction_stats(), "llm": llm_stats()}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "M3.md").write_text(render(stats), encoding="utf-8")
    public = {**stats, "extraction": [{**es, "fields": {f: {k: v for k, v in d.items() if k != "examples"}
                                                         for f, d in es["fields"].items()}}
                                      if es["private"] else es for es in stats["extraction"]]}
    (REPORT_DIR / "M3.json").write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return stats
