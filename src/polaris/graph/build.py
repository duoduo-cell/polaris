"""从 Postgres 重建知识图谱（Neo4j）。北极星自己定义的结构，不用 Graphiti 之类的框架（`TECH.md` 第 2、3 节）。

节点：Company 公司、Unit 部门（按段，如 腾讯的 CSIG → 腾讯地图）、Job 岗位、City 城市、Skill 技能（polaris/vocab.py）、
Direction 方向（抽取时的固定几类）、Post 帖子、Question 面试题。
关系（每条都带 source 来源和 at 时间，回答时能说出依据）：
- Job -AT-> Company，Job -IN-> Unit，Unit -PART_OF-> Unit / Company，Job -LOCATED-> City
- Job -REQUIRES-> Skill（从职位要求里按词表找），Job -DIRECTION-> Direction
- Post -MENTIONS-> Company，Post -ABOUT-> Skill / Direction，Post -ABOUT_UNIT-> Unit，Post -ABOUT_JOB-> Job
- Question -FROM-> Post，Question -ASKED_AT-> Company，Question -TESTS-> Skill

帖子和部门、岗位的关联（自己实现的对齐的一部分）宁可少连不错连：
- 同公司某个部门段的名字（至少两个字）出现在帖子写的团队（team 字段）里才连 ABOUT_UNIT。反过来不行：
  帖子只写"抖音"时不能连到"泛抖音""抖音直播"（10-11 抽检时见过，和同岗识别里部门名字面包含是同一个坑）；
- 在此基础上，帖子写的岗位和这个部门下某个岗位名相近、差异里没有区分词（研发/产品/运营…，同 align/match.py），才连 ABOUT_JOB。

整图重建（几千个节点，一次几秒）；Postgres 是唯一的事实来源，图谱随时可以删了重建。
"""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select

from polaris.align.match import DISTINGUISHING, role_difference
from polaris.align.normalize import key, split_title
from polaris.db.models import InterviewQuestion, Job, Post, RawItem, SearchChunk
from polaris.db.session import session_scope
from polaris.graph.client import driver
from polaris.vocab import skills_in

BATCH = 500
ROLE_LINK = 0.8  # 帖子岗位名和岗位名相似度下限
CONSTRAINTS = [
    "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (n:Company) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT unit_key IF NOT EXISTS FOR (n:Unit) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT job_id IF NOT EXISTS FOR (n:Job) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT city_name IF NOT EXISTS FOR (n:City) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (n:Skill) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT direction_name IF NOT EXISTS FOR (n:Direction) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT post_id IF NOT EXISTS FOR (n:Post) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT question_key IF NOT EXISTS FOR (n:Question) REQUIRE n.key IS UNIQUE",
]


def _same_role(a: str, b: str) -> bool:
    if SequenceMatcher(None, a, b).ratio() < ROLE_LINK:
        return False
    return not any(w in role_difference(a, b) for w in DISTINGUISHING)


def _iso(v: Any) -> str | None:
    return v.isoformat()[:10] if v else None


def _unit_path(company: str, unit: str | None) -> list[dict[str, str]]:
    """"CSIG/腾讯地图" → [{key: 腾讯|csig, name: CSIG}, {key: 腾讯|csig/腾讯地图, name: 腾讯地图}]。"""
    path, names, out = [], [], []
    for seg in (unit or "").split("/"):
        if not key(seg):
            continue
        path.append(key(seg))
        names.append(seg.strip())
        out.append({"key": f"{company}|{'/'.join(path)}", "name": seg.strip(), "label": "/".join(names), "company": company})
    return out


def collect() -> dict[str, list[dict[str, Any]]]:
    """从 Postgres 读出所有节点和关系（纯数据，不连 Neo4j，方便测试）。"""
    with session_scope() as s:
        # 只算在招的：官网每天重采后会标下架（10-12），下架的留在图谱里会让"某公司多少个岗位要求某技能"偏多
        jobs = s.execute(select(Job).where(Job.is_synthetic.is_(False), Job.status == "active").order_by(Job.id)).scalars().all()
        posts = s.execute(select(Post, RawItem.source, RawItem.raw_text)
                          .join(RawItem, RawItem.id == Post.raw_item_id).order_by(Post.id)).all()
        questions = s.execute(select(SearchChunk.key, SearchChunk.post_id, SearchChunk.body, InterviewQuestion.company,
                                     InterviewQuestion.material, InterviewQuestion.evidence)
                              .outerjoin(InterviewQuestion, InterviewQuestion.key == SearchChunk.key)
                              .where(SearchChunk.kind == "question").order_by(SearchChunk.id)).all()
        g: dict[str, list[dict[str, Any]]] = defaultdict(list)
        units_by_company: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
        jobs_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for j in jobs:
            company = j.company or j.company_name or "未知"
            role = split_title(j.title, j.company_name).role_key
            g["jobs"].append({"id": j.id, "key": j.key, "title": j.title, "company": company, "job_type": j.job_type,
                              "posted_at": _iso(j.posted_at), "at": _iso(j.last_seen_at), "source": "jobs"})
            path = _unit_path(company, j.unit)
            for i, u in enumerate(path):
                units_by_company[company][u["key"]] = u
                g["unit_parent"].append({"child": u["key"], "parent": path[i - 1]["key"] if i else None, "company": company})
            if path:
                g["job_unit"].append({"job": j.id, "unit": path[-1]["key"]})
                jobs_by_unit[path[-1]["key"]].append({"id": j.id, "role": role})
            g["job_city"] += [{"job": j.id, "city": c} for c in (j.cities or [])]
            text = "\n".join([j.title or "", j.jd_text or "", *map(str, j.requirements or [])])
            g["job_skill"] += [{"job": j.id, "skill": k} for k in skills_in(text)]
            g["job_direction"] += [{"job": j.id, "direction": d} for d in j.directions or [] if d != "其他"]
        g["units"] = [u for us in units_by_company.values() for u in us.values()]
        posts_by_id = {}
        for p, source, raw_text in posts:
            companies = list(p.big_companies or p.companies or [])
            posts_by_id[p.id] = companies
            g["posts"].append({"id": p.id, "type": p.post_type, "source": source, "published_at": _iso(p.published_at),
                               "summary": (p.summary or "")[:200]})
            g["post_company"] += [{"post": p.id, "company": c, "at": _iso(p.published_at), "source": source}
                                  for c in companies]
            g["post_skill"] += [{"post": p.id, "skill": k} for k in skills_in(f"{p.summary}\n{raw_text}")]
            g["post_direction"] += [{"post": p.id, "direction": d} for d in p.directions or [] if d != "其他"]
            team = key(p.team)
            if len(team) < 2:
                continue
            for c in companies:
                for u in units_by_company.get(c, {}).values():
                    seg = key(u["name"])
                    if len(seg) >= 2 and seg in team:
                        g["post_unit"].append({"post": p.id, "unit": u["key"], "method": "团队名里有这个部门名"})
                        for job in jobs_by_unit.get(u["key"], []):
                            if any(_same_role(key(r), job["role"]) for r in p.roles or []):
                                g["post_job"].append({"post": p.id, "job": job["id"], "method": "部门一致且岗位名相近"})
        for qkey, post_id, body, company, material, evidence in questions:
            g["questions"].append({"key": qkey, "post": post_id, "text": body[:300]})
            if company and material == "firsthand":
                g["question_company"].append({"q": qkey, "company": company, "source": f"P-{post_id}", "evidence": evidence})
            g["question_skill"] += [{"q": qkey, "skill": k} for k in skills_in(body)]
    return dict(g)


CYPHER = {
    "jobs": """UNWIND $rows AS r
        MERGE (c:Company {name: r.company})
        MERGE (j:Job {id: r.id}) SET j.key = r.key, j.title = r.title, j.job_type = r.job_type, j.posted_at = r.posted_at
        MERGE (j)-[e:AT]->(c) SET e.source = r.source, e.at = r.at""",
    "units": "UNWIND $rows AS r MERGE (u:Unit {key: r.key}) SET u.name = r.name, u.label = r.label, u.company = r.company",
    "unit_parent": """UNWIND $rows AS r MATCH (u:Unit {key: r.child})
        CALL (u, r) { WITH u, r WHERE r.parent IS NULL MERGE (c:Company {name: r.company}) MERGE (u)-[:PART_OF]->(c) }
        CALL (u, r) { WITH u, r WHERE r.parent IS NOT NULL MATCH (p:Unit {key: r.parent}) MERGE (u)-[:PART_OF]->(p) }""",
    "job_unit": "UNWIND $rows AS r MATCH (j:Job {id: r.job}), (u:Unit {key: r.unit}) MERGE (j)-[:IN]->(u)",
    "job_city": "UNWIND $rows AS r MATCH (j:Job {id: r.job}) MERGE (c:City {name: r.city}) MERGE (j)-[:LOCATED]->(c)",
    "job_skill": """UNWIND $rows AS r MATCH (j:Job {id: r.job}) MERGE (k:Skill {name: r.skill})
        MERGE (j)-[e:REQUIRES]->(k) SET e.source = 'jd'""",
    "job_direction": "UNWIND $rows AS r MATCH (j:Job {id: r.job}) MERGE (d:Direction {name: r.direction}) MERGE (j)-[:DIRECTION]->(d)",
    "posts": """UNWIND $rows AS r MERGE (p:Post {id: r.id})
        SET p.type = r.type, p.source = r.source, p.published_at = r.published_at, p.summary = r.summary""",
    "post_company": """UNWIND $rows AS r MATCH (p:Post {id: r.post}) MERGE (c:Company {name: r.company})
        MERGE (p)-[e:MENTIONS]->(c) SET e.source = r.source, e.at = r.at""",
    "post_skill": "UNWIND $rows AS r MATCH (p:Post {id: r.post}) MERGE (k:Skill {name: r.skill}) MERGE (p)-[:ABOUT]->(k)",
    "post_direction": "UNWIND $rows AS r MATCH (p:Post {id: r.post}) MERGE (d:Direction {name: r.direction}) MERGE (p)-[:ABOUT]->(d)",
    "post_unit": """UNWIND $rows AS r MATCH (p:Post {id: r.post}), (u:Unit {key: r.unit})
        MERGE (p)-[e:ABOUT_UNIT]->(u) SET e.method = r.method""",
    "post_job": """UNWIND $rows AS r MATCH (p:Post {id: r.post}), (j:Job {id: r.job})
        MERGE (p)-[e:ABOUT_JOB]->(j) SET e.method = r.method""",
    "questions": """UNWIND $rows AS r MATCH (p:Post {id: r.post})
        MERGE (q:Question {key: r.key}) SET q.text = r.text MERGE (q)-[:FROM]->(p)""",
    "question_company": """UNWIND $rows AS r MATCH (q:Question {key: r.q}) MERGE (c:Company {name: r.company})
        MERGE (q)-[e:ASKED_AT]->(c) SET e.source = r.source, e.evidence = r.evidence""",
    "question_skill": "UNWIND $rows AS r MATCH (q:Question {key: r.q}) MERGE (k:Skill {name: r.skill}) MERGE (q)-[:TESTS]->(k)",
}


def rebuild() -> dict[str, Any]:
    g = collect()
    d = driver()
    with d.session() as session:  # 分批删要用自动提交的事务
        session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 2000 ROWS").consume()
    for c in CONSTRAINTS:
        d.execute_query(c)
    for name, cypher in CYPHER.items():  # 顺序有讲究：先节点后关系
        rows = g.get(name, [])
        for i in range(0, len(rows), BATCH):
            d.execute_query(cypher, rows=rows[i:i + BATCH])
    nodes = {r["label"]: r["n"] for r in d.execute_query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label").records}
    rels = {r["type"]: r["n"] for r in d.execute_query(
        "MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS n ORDER BY type").records}
    return {"nodes": nodes, "relationships": rels, "rows": dict(Counter({k: len(v) for k, v in g.items()}))}
