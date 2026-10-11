"""记忆评测（M5）：`eval/gold/memory_v1.json` 里四个虚构求职者的多次对话。

两部分：
1. 记忆操作：每句话按规则改记忆后发生了哪些操作（新增、更新、作废、待确认、追问、确认、拒绝、抑制……），和标准答案逐条比，
   算精确率、召回率；noop 不计。标准答案里 optional 的操作做了不算错、没做不算漏。
2. 问答：对话全部结束后，按 LongMemEval 的五种能力提问（信息提取、跨会话推理、知识更新、时间推理、没说过要说不知道），
   agent 只带记忆工具回答，卡片放在系统提示里；按回答里有没有该有的内容评分。

为了不碰你自己的记忆，整个评测在单独的库 polaris_memeval 里跑，每个人开始前清空记忆表。
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from polaris.config import ROOT

GOLD = ROOT / "eval" / "gold" / "memory_v1.json"  # 开发集：第一次跑完按它改过规则和提示词
HOLDOUT = ROOT / "eval" / "gold" / "memory_holdout_v1.json"  # 留出集：改完之后才写，只评不调
TEST = ROOT / "eval" / "gold" / "memory_test_v2.json"  # 测试集：第三版规则改完之后才写，只评不调
FIRST_RUN = ROOT / "eval" / "reports" / "M5-v1.json"  # 开发集第一次跑的结果（规则和提示词 v1），留作对比
SECOND_RUN = ROOT / "eval" / "reports" / "M5-v2.json"  # 第二版规则在开发集、留出集上的结果
SETS = (("dev", GOLD), ("holdout", HOLDOUT), ("test", TEST))
REPORT_DIR = ROOT / "eval" / "reports"
SIGNIFICANT = {"add", "update", "schedule", "invalidate", "pending", "ask", "suppressed", "confirm", "reject", "forget"}
# "你没说过"一类的说法（10-11 第一次跑时，"你没跟我说过期望薪资"被漏判成答错，补上"没跟我……过"这类）
ABSTAIN = ["没说过", "没跟我说", "没和我说", "没提过", "没跟我提", "没告诉", "没有提", "没有说", "不知道", "没记", "没有记录",
           "没有相关", "没有投", "没投", "没有"]
ABILITY_NAMES = {"IE": "信息提取", "MR": "跨会话推理", "KU": "知识更新", "TR": "时间推理", "ABS": "没说过要说不知道"}
MEMORY_TOOLS = ("memory_card", "memory_history", "memory_known_at")
PROMPT_VERSION = "memory-qa-v1"
QA_PROMPT = """你是北极星，求职者的个人助手。今天是 {today}。下面是你记着的关于用户的事（卡片，编号 M-）：
{card}

回答用户关于他自己情况的问题：
- 先看卡片；问到"以前怎么说的""某天说的是什么"时，用 memory_history 或 memory_known_at 查当时的记录。
- 卡片里没有、历史里也没有的，直接说"你没跟我说过"，不要猜。
- 回答简短，写出依据的记忆编号（M-）。"""


def use_eval_database() -> str:
    """切到单独的评测库（不存在就建、迁移到最新），返回库名。必须在用到数据库之前调用。"""
    from polaris.config import get_settings
    from polaris.db.session import get_engine

    url = make_url(get_settings().database_url).set(database="polaris_memeval")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(text("select 1 from pg_database where datname = 'polaris_memeval'")).scalar():
            conn.execute(text('create database "polaris_memeval"'))
    admin.dispose()
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    get_engine.cache_clear()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return "polaris_memeval"


def _norm(v: str | None) -> str:
    return re.sub(r"[\s\-年月日号]", "", (v or "").lower())


def _match(pred: dict[str, Any], gold: dict[str, Any]) -> bool:
    from polaris.memory.schema import qualifier_key

    if pred["op"] != gold["op"] or pred["attribute"] != gold["attr"]:
        return False
    if gold.get("q") and qualifier_key(pred.get("qualifier")) != qualifier_key(gold["q"]):
        return False
    pv = _norm(pred.get("value"))
    return any(alt == "" or _norm(alt) in pv or (pv and pv in _norm(alt)) for alt in gold["value"])


def score_turn(preds: list[dict[str, Any]], golds: list[dict[str, Any]]) -> dict[str, Any]:
    preds = [p for p in preds if p["op"] in SIGNIFICANT]
    used: set[int] = set()
    tp, fn, matched_optional = [], [], 0
    for g in sorted(golds, key=lambda g: g.get("optional", False)):  # 先配必须的
        hit = next((i for i, p in enumerate(preds) if i not in used and _match(p, g)), None)
        if hit is None:
            if not g.get("optional"):
                fn.append(g)
            continue
        used.add(hit)
        if g.get("optional"):
            matched_optional += 1
        else:
            tp.append(g)
    fp = [p for i, p in enumerate(preds) if i not in used]
    return {"tp": tp, "fn": fn, "fp": fp, "exact": not fn and not fp}


def run(k: int = 1, gold_path=GOLD) -> dict[str, Any]:
    use_eval_database()
    from polaris.agent.minimal import Profile, ask
    from polaris.db.session import session_scope
    from polaris.eval.suites.tool_calling import check_values
    from polaris.llm.client import LLM
    from polaris.memory.extract import remember
    from polaris.memory.update import card, render_card
    from polaris.runs import run_scope

    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    asked_at = datetime.fromisoformat(gold["asked_at"])
    llm = LLM()
    turns: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    with run_scope("eval", {"module": "M5", "suite": gold["version"], "k": k}) as (run_id, _):
        for p in gold["personas"]:
            with session_scope() as s:
                s.execute(text("truncate memory_events, memory_facts, memory_suppressions restart identity cascade"))
            for si, sess in enumerate(p["sessions"]):
                at = datetime.fromisoformat(sess["at"])
                for ti, t in enumerate(sess["turns"]):
                    now = at + timedelta(minutes=2 * ti)
                    try:
                        out = remember(t["user"], llm=llm, now=now, thread_id=f"{p['id']}-{si}", run_id=run_id)
                        err = None
                    except Exception as e:  # noqa: BLE001
                        out, err = {"events": [], "questions": []}, f"{type(e).__name__}: {e}"[:200]
                    sc = score_turn(out["events"], t["expect"])
                    turns.append({"persona": p["id"], "session": si, "turn": ti, "user": t["user"], "error": err,
                                  "events": [{k_: e[k_] for k_ in ("op", "attribute", "qualifier", "value", "reason")}
                                             for e in out["events"]],
                                  "questions": out["questions"], **sc})
            with session_scope() as s:
                card_text = render_card(card(s, asked_at.date()))
            profile = Profile("memory", PROMPT_VERSION, QA_PROMPT.format(today=asked_at.date().isoformat(), card=card_text),
                              MEMORY_TOOLS, max_model_calls=6)
            for q in p["questions"]:
                for i in range(k):
                    r = ask(q["q"], run_id=run_id, thread_id=f"{q['id']}#{i}", purpose="m5_memory_qa", profile=profile)
                    must = [ABSTAIN] if q.get("abstain") else q["must"]
                    ok = r.error is None and check_values(must, r.answer)
                    answers.append({"persona": p["id"], "id": q["id"], "ability": q["ability"], "k": i, "question": q["q"],
                                    "answer": r.answer, "ok": ok, "error": r.error,
                                    "tools": [c["tool"] for c in r.tool_calls]})
    return summarize(turns, answers, k, gold["version"])


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r,
            "f1": 2 * p * r / (p + r) if p and r else None}


def summarize(turns: list[dict[str, Any]], answers: list[dict[str, Any]], k: int, version: str) -> dict[str, Any]:
    from polaris.config import get_settings
    from polaris.runs import git_commit

    by_op: dict[str, Counter] = defaultdict(Counter)
    for t in turns:
        for g in t["tp"]:
            by_op[g["op"]]["tp"] += 1
        for g in t["fn"]:
            by_op[g["op"]]["fn"] += 1
        for p in t["fp"]:
            by_op[p["op"]]["fp"] += 1
    total = Counter()
    for c in by_op.values():
        total.update(c)
    per_q: dict[str, list[bool]] = defaultdict(list)
    for a in answers:
        per_q[a["id"]].append(a["ok"])
    abilities: dict[str, dict[str, Any]] = {}
    for ab in ABILITY_NAMES:
        qs = sorted({a["id"] for a in answers if a["ability"] == ab})
        if qs:
            runs = [a for a in answers if a["ability"] == ab]
            abilities[ab] = {"questions": len(qs), "runs": len(runs), "ok": sum(a["ok"] for a in runs),
                             "pass_k": sum(all(per_q[q]) for q in qs)}
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "git_commit": git_commit(),
            "suite": version, "k": k, "model": get_settings().model_for("extract"),
            "turns": len(turns), "turns_exact": sum(t["exact"] for t in turns), "turn_errors": sum(bool(t["error"]) for t in turns),
            "ops": _prf(total["tp"], total["fp"], total["fn"]),
            "by_op": {op: _prf(c["tp"], c["fp"], c["fn"]) for op, c in sorted(by_op.items())},
            "questions": len(per_q), "answers_ok": sum(a["ok"] for a in answers), "answers": len(answers),
            "pass_k": sum(all(v) for v in per_q.values()), "abilities": abilities,
            "turn_details": turns, "answer_details": answers}


def render_set(r: dict[str, Any], name: str) -> list[str]:
    from polaris.eval.m3 import fmt, pct

    o = r["ops"]
    lines = [f"## {name}：{r['suite']}（{r['turns']} 句话、{r['questions']} 个问题）", "",
             "### 记忆操作（每句话改了什么）", "",
             "| 指标 | 结果 |", "|---|---|",
             f"| 操作的精确率 / 召回率 / F1 | {fmt(o['precision'])} / {fmt(o['recall'])} / {fmt(o['f1'])}"
             f"（对 {o['tp']}，多 {o['fp']}，漏 {o['fn']}） |",
             f"| 整句完全对（该做的都做了、没多做） | {r['turns_exact']}/{r['turns']}（{pct(r['turns_exact'], r['turns'])}） |",
             f"| 抽取出错 | {r['turn_errors']} 句 |", "",
             "| 操作 | 对 | 多 | 漏 | 精确率 | 召回率 |", "|---|---|---|---|---|---|"]
    for op, m in r["by_op"].items():
        lines.append(f"| {op} | {m['tp']} | {m['fp']} | {m['fn']} | {fmt(m['precision'])} | {fmt(m['recall'])} |")
    wrong = [t for t in r["turn_details"] if not t["exact"]]
    if wrong:
        lines += ["", "没完全对的句子：", "", "| 谁 | 那句话 | 漏了 | 多了 |", "|---|---|---|---|"]
        for t in wrong:
            miss = "；".join(f"{g['op']} {g['attr']}{('（' + g['q'] + '）') if g.get('q') else ''}" for g in t["fn"]) or "—"
            extra = "；".join(f"{p['op']} {p['attribute']}={p['value']}" for p in t["fp"]) or "—"
            lines.append(f"| {t['persona']} | {t['user']} | {miss} | {extra} |")
    lines += ["", f"### 问答（LongMemEval 的五种能力，每题跑 {r['k']} 次）", "",
              "| 能力 | 题数 | 单次答对 | 每次都对 |", "|---|---|---|---|"]
    for ab, m in r["abilities"].items():
        lines.append(f"| {ABILITY_NAMES[ab]}（{ab}） | {m['questions']} | {m['ok']}/{m['runs']} | {m['pass_k']}/{m['questions']} |")
    lines.append(f"| **合计** | {r['questions']} | {r['answers_ok']}/{r['answers']}（{pct(r['answers_ok'], r['answers'])}） | "
                 f"**{r['pass_k']}/{r['questions']}** |")
    bad = [a for a in r["answer_details"] if not a["ok"]]
    if bad:
        lines += ["", "答错的：", "", "| 题 | 问题 | 回答 |", "|---|---|---|"]
        for a in bad:
            lines.append(f"| {a['id']} | {a['question']} | {(a['error'] or a['answer']).replace(chr(10), ' ')[:120]} |")
    return lines + [""]


def render(results: dict[str, dict[str, Any]]) -> str:
    from polaris.eval.m3 import fmt

    first = next(iter(results.values()))
    lines = ["# M5 指标：记忆", "", f"生成于 {first['generated_at']}，命令 `polaris metrics m5`；抽取模型 `{first['model']}`。", "",
             "题目是人写的虚构求职者（可公开），每句话标出应当发生的记忆操作，最后按 LongMemEval 的五种能力提问。"
             "模型只抽候选事实，新增、更新、作废、追问等决定由规则做（`src/polaris/memory/update.py`），这里比的是最终发生的操作。"
             "和同岗识别一样分开发集和留出集：开发集第一次跑完后按它改过规则和提示词，留出集是改完之后才写的。", ""]
    if FIRST_RUN.exists():
        v1 = json.loads(FIRST_RUN.read_text(encoding="utf-8"))
        lines += [f"**第一版**（开发集）：操作 F1 {fmt(v1['ops']['f1'])}，整句完全对 {v1['turns_exact']}/{v1['turns']}，"
                  f"问答 {v1['answers_ok']}/{v1['answers']}（其中 2 题其实答对了，是评分没认出\"你没跟我说过\"这种说法，已修评分）。"
                  "据此改了：进度类属性不排到将来、否认某公司时牵连的推断一并作废并按公司抑制、提示词说清\"研二\"这类算亲口说的。", ""]
    if SECOND_RUN.exists():
        v2 = json.loads(SECOND_RUN.read_text(encoding="utf-8"))
        h = v2["holdout"]
        lines += [f"**第二版**：开发集操作 F1 {fmt(v2['dev']['ops']['f1'])}；留出集（改完第一版才写）操作 F1 {fmt(h['ops']['f1'])}、"
                  f"整句全对 {h['turns_exact']}/{h['turns']}、问答 {h['answers_ok']}/{h['answers']}。"
                  "两份里都出现\"该问没问\"：用户换了个数但没说要改，模型标成改口，规则直接覆盖了；实测时也遇到。据此改了第三版："
                  "是否改口由代码再核对一遍明确的改口词（改、现在、其实、还是、说错……），"
                  "以及问\"以哪个为准\"时用户重申原来的说法，待确认的新说法作废。改完才写了测试集 v2。", ""]
    names = {"memory-v1": "开发集（第一版之后用来改过规则）", "memory-holdout-v1": "留出集（第二版之后用来改过规则）",
             "memory-test-v2": "**测试集**（第三版改完才写，只评不调）"}
    for r in results.values():
        lines += render_set(r, names.get(r["suite"], r["suite"]))
    return "\n".join(lines)


def write_report(results: dict[str, dict[str, Any]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "M5.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
                                        newline="\n")
    (REPORT_DIR / "M5.md").write_text(render(results), encoding="utf-8", newline="\n")
