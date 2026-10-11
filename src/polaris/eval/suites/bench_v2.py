"""Polaris-Bench v2（M4b）：比 real-v1 难的工具调用题——比较、图谱统计、面试题、按时间筛、跨来源冲突、带条件的推荐。

题目的标准答案在建题时从库里（Postgres 和图谱）现算，符合条件的都算对，数据增加了题目仍然成立：
- 比较、统计题：数字要和图谱统计一致；
- 推荐题：至少写出 N 个符合条件（城市、类型、方向）的岗位编号，**写出任何一个不符合条件的岗位编号就算错**（cite_only）；
- 冲突题：两种说法都要说出来（合成数据里的转载版故意改了字段，真实数据目前没有跨来源冲突，见 `design/M4.md`）；
- 找不到的题：建题时先确认库里确实没有。
"""
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from polaris.db.models import Job, Post, SearchChunk
from polaris.db.session import session_scope
from polaris.eval.suites.real_v1 import NOT_FOUND
from polaris.eval.suites.tool_calling import Task
from polaris.graph.client import driver, enabled
from polaris.vocab import SKILLS

SUITE_VERSION = "polaris-bench-v2"
COMPANY_FORMS = {"字节跳动": ["字节", "ByteDance", "Bytedance"], "腾讯": ["腾讯", "Tencent"], "小红书": ["小红书", "Xiaohongshu", "RED"],
                 "百度": ["百度", "Baidu"], "阿里巴巴": ["阿里", "Alibaba"]}


def _forms(company: str) -> list[str]:
    return COMPANY_FORMS.get(company, [company])


def _graph(cypher: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in driver().execute_query(cypher, **params).records]


def _jobs_by_skill(skill: str) -> dict[str, list[int]]:
    rows = _graph("MATCH (j:Job)-[:REQUIRES]->(:Skill {name: $s}), (j)-[:AT]->(c:Company) RETURN c.name AS c, j.id AS id", s=skill)
    out: dict[str, list[int]] = {}
    for r in rows:
        out.setdefault(r["c"], []).append(r["id"])
    return out


def _skill_short(name: str) -> list[str]:
    return [p.strip() for p in name.split("/") if p.strip()]


def build_tasks() -> list[Task]:
    if not enabled():
        raise RuntimeError("Polaris-Bench v2 要用知识图谱：先配好 NEO4J_PASSWORD 并运行 polaris index")
    tasks: list[Task] = []
    now = datetime.now(UTC)

    # 图谱统计、比较
    lg = _jobs_by_skill("LangGraph")
    lg_ids = [f"J-{i}" for ids in lg.values() for i in ids]
    ranked = sorted(lg.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    q_lg = "采到的大厂岗位里，哪些公司的职位描述或要求写了 LangGraph？各有几个？每家举一个岗位编号。"
    tasks.append(Task("kg-langgraph", "kg", q_lg, [_forms(c) for c, _ in ranked[:2]] + [[str(len(ranked[0][1]))]], lg_ids))
    tasks.append(Task("en-langgraph", "english", "Which companies have job postings that mention LangGraph? Cite one job id for each.",
                      [_forms(c) for c, _ in ranked[:2]], lg_ids))
    tasks.append(Task("recovery-kg", "recovery", q_lg, [_forms(c) for c, _ in ranked[:2]], lg_ids,
                      faults={"kg_stats": ["rate_limit"]}))
    tx, bd = len(lg.get("腾讯", [])), len(lg.get("字节跳动", []))
    tasks.append(Task("cmp-langgraph", "compare", "腾讯和字节，哪家要求 LangGraph 的岗位更多？各多少个？",
                      [["腾讯"], [str(tx)], [str(bd)]]))
    units = _graph("""MATCH (j:Job)-[:AT]->(:Company {name: '腾讯'}), (j)-[:DIRECTION]->(:Direction {name: 'Agent'}),
                      (j)-[:IN]->(u:Unit) RETURN u.label AS u, collect(j.id) AS ids ORDER BY size(ids) DESC, u""")
    top_units = [u for u in units if "/" not in u["u"]][:2]  # 事业群一级
    unit_jobs = _graph("""MATCH (j:Job)-[:AT]->(:Company {name: '腾讯'}), (j)-[:DIRECTION]->(:Direction {name: 'Agent'}),
                          (j)-[:IN]->(u:Unit) WHERE u.label = $a OR u.label STARTS WITH $a + '/' OR u.label = $b
                          OR u.label STARTS WITH $b + '/' RETURN j.id AS id""", a=top_units[0]["u"], b=top_units[1]["u"])
    tasks.append(Task("kg-tencent-units", "kg", "腾讯在招 Agent 方向岗位最多的是哪两个事业群？各举一个岗位编号。",
                      [[top_units[0]["u"]], [top_units[1]["u"]]], [f"J-{r['id']}" for r in unit_jobs]))
    cities = {r["c"]: r["n"] for r in _graph("""MATCH (j:Job)-[:DIRECTION]->(:Direction {name: '多模态'}), (j)-[:LOCATED]->(c:City)
                                               RETURN c.name AS c, count(DISTINCT j) AS n""")}
    tasks.append(Task("cmp-multimodal-city", "compare", "多模态方向的岗位，北京和上海哪个城市更多？各多少个？",
                      [["北京"], [str(cities.get("北京", 0))], [str(cities.get("上海", 0))]]))
    q_skills = _graph("""MATCH (q:Question)-[:ASKED_AT]->(:Company {name: '字节跳动'}), (q)-[:TESTS]->(k:Skill), (q)-[:FROM]->(p:Post)
                         RETURN k.name AS k, count(DISTINCT q) AS n, collect(DISTINCT p.id) AS posts ORDER BY n DESC, k""")
    top = q_skills[0]
    tasks.append(Task("kg-bd-question-skills", "kg", "字节的面经里，面试最常考哪几类技能？说前三个，并给出处。",
                      [_skill_short(top["k"])], [f"P-{p}" for p in top["posts"]]))

    with session_scope() as s:
        # 面试题
        def question_posts(pattern: str, company: str | None = None) -> list[str]:
            stmt = select(SearchChunk.post_id, SearchChunk.body).where(SearchChunk.kind == "question")
            if company:
                stmt = stmt.where(SearchChunk.companies.contains([company]))
            return sorted({f"P-{pid}" for pid, body in s.execute(stmt) if re.search(pattern, body, re.I)})

        kv = question_posts(r"kv.?cache")
        kv_companies = sorted({c for (cs,) in s.execute(select(SearchChunk.companies).where(
            SearchChunk.kind == "question", SearchChunk.body.op("~*")(r"kv.?cache"))) for c in cs})
        q_kv = "哪些公司的面试问过 KV Cache 相关的问题？各给一个出处编号。"
        tasks.append(Task("q-kvcache", "questions", q_kv, [sum((_forms(c) for c in kv_companies), [])], kv))
        tasks.append(Task("recovery-questions", "recovery", q_kv, [sum((_forms(c) for c in kv_companies), [])], kv,
                          faults={"questions_search": ["timeout"]}))
        mem = question_posts(r"记忆|memory", "字节跳动")
        tasks.append(Task("q-bd-memory", "questions", "字节面试里问过哪些 Agent 记忆（memory）设计的问题？列两道并分别给出处编号。",
                          [["记忆", "memory", "Memory"]], mem, cite_min=2))

        # 按时间筛
        recent = [f"P-{i}" for i in s.execute(select(Post.id).where(
            Post.post_type == "面经", Post.big_companies.contains(["字节跳动"]),
            Post.published_at >= now - timedelta(days=7))).scalars()]
        if len(recent) >= 3:
            tasks.append(Task("time-bd-mianjing", "time", "最近 7 天有哪些字节的面经帖？列三条，写出编号和大概问了什么。",
                              [], recent, cite_min=3, cite_only="P-"))

        # 跨来源冲突（合成数据）、多城市合并
        syn = s.execute(select(Job).where(Job.is_synthetic.is_(True), Job.conflicts != []).order_by(Job.id)).scalars().all()
        salary = next(j for j in syn if any(c["field"] == "salary_min" for c in j.conflicts))
        alt = next(c for c in salary.conflicts if c["field"] == "salary_min")["alternatives"][0]["value"]
        alt_max = next(c for c in salary.conflicts if c["field"] == "salary_max")["alternatives"][0]["value"]
        tasks.append(Task("conflict-salary", "conflict", f"J-{salary.id} 这个岗位日薪多少？各处说法一致吗？",
                          [[str(int(salary.salary_min))], [str(int(salary.salary_max))], [str(int(alt))], [str(int(alt_max))]],
                          [f"J-{salary.id}"]))
        days = next(j for j in syn if any(c["field"] == "days_per_week" for c in j.conflicts))
        d_alt = next(c for c in days.conflicts if c["field"] == "days_per_week")["alternatives"][0]["value"]
        tasks.append(Task("conflict-days", "conflict", f"J-{days.id} 每周要到岗几天？各个来源说法一样吗？",
                          [[f"{days.days_per_week}天", f"{days.days_per_week} 天"], [f"{d_alt}天", f"{d_alt} 天"]],
                          [f"J-{days.id}"]))
        multi = s.execute(select(Job).where(Job.is_synthetic.is_(False), Job.title.ilike("%AI Platform%"),
                                            Job.title.ilike("%大模型算法%")).order_by(Job.posting_count.desc())).scalars().first()
        if multi is not None and len(multi.cities) >= 3:
            tasks.append(Task("merged-cities", "lookup", f"字节的「{multi.title}」在哪些城市招？",
                              [[c] for c in multi.cities], [f"J-{multi.id}"]))

        # 带条件的推荐
        def job_ids(*conds: Any) -> list[str]:
            return [f"J-{i}" for i in s.execute(select(Job.id).where(
                Job.is_synthetic.is_(False), Job.status == "active", Job.company_id.is_not(None), *conds)).scalars()]

        agent_like = or_(Job.title.ilike("%agent%"), Job.title.ilike("%智能体%"), Job.directions.contains(["Agent"]))
        bj = job_ids(Job.job_type == "intern", Job.city.ilike("%北京%"), agent_like)
        tasks.append(Task("rec-bj-agent", "recommend", "我在北京，想找大厂 Agent 方向的实习，帮我挑 3 个岗位，写出编号和理由。",
                          [], bj, cite_min=3, cite_only="J-"))
        hz = job_ids(Job.job_type == "intern", Job.city.ilike("%杭州%"), or_(Job.title.ilike("%评测%"), Job.jd_text.ilike("%评测%")))
        tasks.append(Task("rec-hz-eval", "recommend", "杭州有没有做大模型或 Agent 评测相关的实习？推荐 2 个，写出编号。",
                          [], hz, cite_min=2, cite_only="J-"))

        # 找不到要说找不到（先确认库里确实没有）
        none = job_ids(Job.company == "腾讯", Job.city.ilike("%成都%"), Job.job_type == "intern")
        if not none:
            tasks.append(Task("abstain-tx-chengdu", "abstain", "腾讯在成都有没有 Agent 方向的实习岗位？", [NOT_FOUND]))
    tasks.append(Task("irrelevant-rewrite", "irrelevant",
                      "帮我把这句话改得更简洁：我对大模型应用开发很有热情，非常希望能有机会加入贵公司。", expect_tools=False))
    missing = [t.id for t in tasks if t.expect_tools and t.category not in ("abstain", "compare") and not t.cite]
    if missing:
        raise RuntimeError(f"这些题没算出标准答案：{missing}")
    assert all(name in SKILLS for name in ("LangGraph", "KV Cache"))
    return tasks
