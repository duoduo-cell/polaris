"""独立合成演示数据。默认只检查；--seed --apply才写入，不复制或清理任何数据库。"""
import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select, table, text
from sqlalchemy.orm import Session

from polaris.companies import DEMO_COMPANY
from polaris.config import Settings, get_settings, require_demo_database_url
from polaris.db.models import Base, InterviewQuestion, Job, MemoryFact, Post, Posting, RawItem, Run, SearchChunk
from polaris.db.session import get_engine, session_scope
from polaris.interviews import profile
from polaris.memory.schema import value_key
from polaris.search.chunking import Chunk, chunk_values, job_chunks

VERSION = "demo-fixture-v3"
DISCLOSURE = "纯合成演示，预置岗位、面经、公司名及档案均为人工虚构，与实际招聘无关。"
COMPANY = DEMO_COMPANY.name


@dataclass
class DemoRows:
    groups: list[list[Any]]  # 空库内固定编号，按外键依赖顺序插入

    @property
    def counts(self) -> dict[str, int]:
        return {type(rows[0]).__tablename__: len(rows) for rows in self.groups if rows}


def fixture(now: datetime) -> DemoRows:
    """只在内存构造，零网络／数据库调用。固定编号仅允许在全新空库首次导入。"""
    if now.tzinfo is None:
        raise ValueError("合成资料基准时刻必须带时区")
    now = now.astimezone(UTC)
    published, fetched = now - timedelta(days=4), now - timedelta(days=2)
    specs = [
        ("Agent工具工程实习（合成）", "上海、北京", "Agent", 4, 6, "本科及以上在读，论文不是必要条件。"),
        ("Agent应用后端实习（合成）", "上海", "Agent", 3, 3, "本科及以上在读，有工程项目经验优先。"),
        ("Agent研究实习（合成）", "上海", "Agent", 4, 6, "硕士及以上在读，至少发表一篇论文。"),
        ("RAG应用实习（合成，天数未知）", "上海", "RAG", None, 6, "本科及以上在读。"),
        ("RAG评测实习（合成）", "北京", "RAG", 3, 3, "本科及以上在读，论文非必需。"),
    ]
    raws, jobs, postings, chunks = [], [], [], []
    for n, (title, city, direction, days, months, degree) in enumerate(specs, 1):
        requirements = [degree, f"实习至少{months}个月。", f"负责{direction}工具调用与可靠性评测，记录延迟、失败及恢复结果。"]
        if days is not None:
            requirements.append(f"每周至少到岗{days}天。")
        jd = f"{DISCLOSURE}\n职责：负责{direction}应用的工程开发。\n任职要求\n" + "\n".join(requirements)
        raw = RawItem(id=n, source="demo", tier="A", kind="job", external_id=f"{VERSION}:job:{n}",
                      url=f"https://example.invalid/polaris-demo/jobs/{n}", raw_text=jd,
                      raw_json={"demo_fixture": VERSION, "datePosted": published.isoformat()},
                      content_hash=hashlib.sha256(jd.encode()).hexdigest(), fetched_at=fetched,
                      first_seen_at=fetched, last_seen_at=now, published_at=published,
                      fetch_method="synthetic", collector_version=VERSION, is_synthetic=True)
        values = dict(company_name=COMPANY, title=title, directions=[direction], jd_text=jd,
                      requirements=requirements, city=city, job_type="intern", posted_at=published.date(),
                      duration_months=months, days_per_week=days)
        job = Job(id=n, key=f"{VERSION}:job:{n}", company=COMPANY, unit="合成应用组", cities=city.split("、"),
                  first_seen_at=fetched, last_seen_at=now, status="active", posting_count=1, conflicts=[],
                  field_sources={}, is_synthetic=True, **values)
        posting = Posting(id=n, raw_item_id=n, job_id=n, is_synthetic=True, **values)
        raws.append(raw)
        jobs.append(job)
        postings.append(posting)
        chunks.extend(SearchChunk(**chunk_values(c)) for c in job_chunks(job))
    question = "如何评测Agent工具超时后的重试和恢复是否正确？"
    post_text = f"{COMPANY}Agent一面与二面面经（合成）\n{DISCLOSURE}\n一面问了系统设计，二面交流工程项目。\n面试官问：{question}"
    raws.append(RawItem(id=6, source="demo", tier="A", kind="post", external_id=f"{VERSION}:post:1",
                        url="https://example.invalid/polaris-demo/interviews/1", raw_text=post_text,
                        raw_json={"demo_fixture": VERSION, "title": post_text.splitlines()[0]},
                        content_hash=hashlib.sha256(post_text.encode()).hexdigest(), fetched_at=fetched,
                        first_seen_at=fetched, last_seen_at=now, published_at=published,
                        fetch_method="synthetic", collector_version=VERSION, is_synthetic=True))
    post = Post(id=1, raw_item_id=6, post_type="面经", companies=[COMPANY], big_companies=[],
                roles=["Agent"], directions=["Agent"], job_type="intern", summary=post_text,
                interview_questions=[question], evidence_profile={}, published_at=published)
    # 与正常同步路径复用同一原文解析；不手填公司／轮次绕过来源规则。
    q = InterviewQuestion(id=1, **profile(post, post_text, raws[-1].raw_json)["questions"][0])
    for c in [Chunk(key="post:1", kind="post", header=f"{COMPANY}｜合成面经", body=post_text,
                    post_id=1, companies=(COMPANY,), published_at=published, is_synthetic=True),
              Chunk(key=q.key, kind="question", header=f"{COMPANY}｜Agent｜合成面经题", body=question,
                    post_id=1, companies=(COMPANY,), published_at=published, is_synthetic=True)]:
        chunks.append(SearchChunk(**chunk_values(c)))
    facts = []
    for attr, value in [("degree", "本科在读"), ("paper_experience", "无论文"), ("target_city", "北京"),
                        ("target_role", "Agent"), ("job_type", "日常实习"), ("days_per_week", "4"),
                        ("skill", "Python"), ("skill", "RAG")]:
        facts.append(MemoryFact(attribute=attr, value=value, value_key=value_key(attr, value), statement=f"合成档案：{value}",
                                kind="stated", status="active", source_text=f"{DISCLOSURE} 合成档案：{attr}={value}",
                                recorded_at=fetched, last_confirmed_at=fetched, valid_from=published.date()))
    return DemoRows([raws, jobs, postings, [post], [q], chunks, facts])


def require_demo_target(settings: Settings, engine: Engine) -> None:
    if not settings.demo_mode:
        raise ValueError("仅DEMO_MODE允许合成演示导入")
    # 不仅核对配置，也核对实际注入的引擎，防止调用方意外传入个人库。
    for url in (settings.database_url, engine.url):
        require_demo_database_url(url)


def assert_ready(session: Session) -> None:
    marker = session.scalar(select(Run).where(Run.kind == "demo-seed", Run.status == "ok"))
    if marker is None or (marker.params or {}).get("fixture") != VERSION:
        raise ValueError("演示库尚未导入当前合成资料，拒绝启动")
    for model in (RawItem, Job, Posting, SearchChunk):
        if session.scalar(select(func.count()).select_from(model).where(model.is_synthetic.is_not(True))):
            raise ValueError("演示库混入非合成标记资料，拒绝启动；未删除任何记录")
    if session.scalar(select(func.count()).select_from(RawItem).where(RawItem.source != "demo")):
        raise ValueError("演示库存在其他来源，拒绝启动；未删除任何记录")


def seed(engine: Engine, settings: Settings, now: datetime) -> dict[str, Any]:
    require_demo_target(settings, engine)
    rows = fixture(now)
    with session_scope(engine) as s:
        s.execute(text("select pg_advisory_xact_lock(739514629)"))
        marker = s.scalar(select(Run).where(Run.kind == "demo-seed", Run.status == "ok"))
        if marker is not None:
            assert_ready(s)
            return {"status": "already_seeded", "fixture": VERSION}
        for business_table in Base.metadata.sorted_tables:
            if s.scalar(select(func.count()).select_from(business_table)):
                raise ValueError("目标库并非空库，拒绝导入；不会清空或覆盖已有资料")
        # LangGraph检查点不在业务metadata里，不能把只剩模型历史的库误判成空库。
        for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"):
            if (s.scalar(select(func.to_regclass(f"public.{name}"))) is not None
                    and s.scalar(select(func.count()).select_from(table(name, schema="public")))):
                raise ValueError("目标库存在检查点记录，拒绝导入；不会清理模型历史")
        for group in rows.groups:
            s.add_all(group)
            s.flush()
        # 空库手工编号后同步序列，避免后续正常写入碰撞。仅作用于本次已导入的具体表。
        for name in ("raw_items", "jobs", "postings", "posts", "interview_questions"):
            s.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), (SELECT max(id) FROM {name}), true)"))
        s.add(Run(kind="demo-seed", params={"fixture": VERSION}, status="ok", stats=rows.counts,
                  started_at=now, finished_at=now))
    return {"status": "seeded", "fixture": VERSION, "counts": rows.counts}


# ---- 跟进卡演示剧本：每推进一步改一点合成资料，再检查一次，演示变化怎样被发现、分级、写进报告 ----

WATCH_TITLE = "上海 Agent 日常实习（合成演示）"
WATCH_CRITERIA = {"city": "上海", "job_type": "intern", "direction": "Agent", "max_days_per_week": 4, "big_only": False}
SCRIPT = [
    "重点岗位“Agent工具工程实习”把每周到岗从 4 天改成 5 天（和你最多 4 天冲突）",
    "重点岗位“Agent应用后端实习”下架",
    "上海出现一个新的 Agent 日常实习",
    "出现一篇新面经，和一位同方向求职者拿到 offer 的自述",
]


def _demo_raw(s: Session, kind: str, key: str, text_: str, now: datetime, **extra: Any) -> RawItem:
    raw = RawItem(source="demo", tier="A", kind=kind, external_id=f"{VERSION}:{key}", url=f"https://example.invalid/polaris-demo/{key}",
                  raw_text=text_, raw_json={"demo_fixture": VERSION, **extra}, content_hash=hashlib.sha256(text_.encode()).hexdigest(),
                  fetched_at=now, first_seen_at=now, last_seen_at=now, published_at=now - timedelta(hours=1),
                  fetch_method="synthetic", collector_version=VERSION, is_synthetic=True)
    s.add(raw)
    s.flush()
    return raw


def _apply_step(s: Session, step: int, now: datetime) -> None:
    from polaris.db.models import PeerProfile

    if step == 0:
        job = s.get(Job, 1)
        job.days_per_week = 5
        job.requirements = [r.replace("每周至少到岗4天", "每周至少到岗5天") for r in job.requirements]
        job.jd_text = (job.jd_text or "").replace("每周至少到岗4天", "每周至少到岗5天")
    elif step == 1:
        job = s.get(Job, 2)
        job.status, job.last_seen_at = "gone", now - timedelta(days=2)
    elif step == 2:
        requirements = ["本科及以上在读。", "实习至少3个月。", "负责Agent平台的工具接入与评测。", "每周至少到岗4天。"]
        jd = f"{DISCLOSURE}\n职责：负责Agent平台开发。\n任职要求\n" + "\n".join(requirements)
        raw = _demo_raw(s, "job", "job:new-1", jd, now)
        values = dict(company_name=COMPANY, title="Agent平台开发实习（合成·新）", directions=["Agent"], jd_text=jd,
                      requirements=requirements, city="上海", job_type="intern", posted_at=now.date(), duration_months=3, days_per_week=4)
        job = Job(key=f"{VERSION}:job:new-1", company=COMPANY, unit="合成平台组", cities=["上海"], first_seen_at=now,
                  last_seen_at=now, status="active", posting_count=1, conflicts=[], field_sources={}, is_synthetic=True, **values)
        s.add(job)
        s.flush()
        s.add(Posting(raw_item_id=raw.id, job_id=job.id, is_synthetic=True, **values))
        for c in job_chunks(job):
            s.add(SearchChunk(**chunk_values(c)))
    elif step == 3:
        question = "工具调用失败后怎么恢复，怎么证明恢复是对的？"
        text_ = f"{COMPANY}Agent日常实习一面面经（合成·新）\n{DISCLOSURE}\n本科在读，一段小厂实习。\n面试官问：{question}"
        raw = _demo_raw(s, "post", "post:new-1", text_, now, title=text_.splitlines()[0])
        s.add(Post(raw_item_id=raw.id, post_type="面经", companies=[COMPANY], big_companies=[], roles=["Agent"], directions=["Agent"],
                   job_type="intern", summary=text_, interview_questions=[question],
                   evidence_profile={}, published_at=now - timedelta(hours=1), created_at=now))
        s.add(PeerProfile(raw_item_id=raw.id, source="demo", published_at=now - timedelta(hours=1), who="作者", stage="本科",
                          school="双非一本", internships=[{"company": "小厂"}], target_company=COMPANY, target_role="Agent 日常实习",
                          job_type="日常实习", direction="Agent", outcome="拿到offer", reached="二面",
                          evidence="本科在读，一段小厂实习，二面后拿到 offer（合成）", prompt_version=VERSION, created_at=now))


def advance(s: Session, now: datetime | None = None) -> dict[str, Any]:
    """演示剧本推进一步。第一次只开一张跟进卡并建立基线；之后每次改一点合成资料再检查。只在演示库里运行。"""
    from polaris.db.models import Watch
    from polaris.watch import check, create

    if not get_settings().demo_mode:
        raise ValueError("演示剧本只能在合成演示库里运行")
    assert_ready(s)
    now = now or datetime.now(UTC)
    w = s.scalar(select(Watch).where(Watch.title == WATCH_TITLE, Watch.status != "ended"))
    if w is None:
        w = create(s, title=WATCH_TITLE, criteria=WATCH_CRITERIA, job_ids=[1, 2], until=(now + timedelta(days=14)).date(), now=now)
        result = check(s, w, now)
        return {"step": 0, "done": None, "next": SCRIPT[0], "watch": w.id, "check": result,
                "message": "已开一张演示跟进卡，并记下现在的样子；再推进一步看变化怎样被发现。"}
    step = s.scalar(select(func.count()).select_from(Run).where(Run.kind == "demo-advance")) or 0
    if step >= len(SCRIPT):
        return {"step": step, "done": None, "next": None, "watch": w.id, "message": "剧本已经走完；可以点“现在写报告”看态势分析。"}
    _apply_step(s, step, now)
    s.add(Run(kind="demo-advance", params={"step": step + 1, "fixture": VERSION}, status="ok", stats={},
              started_at=now, finished_at=now))
    s.flush()
    if w.status != "active":
        w.status = "active"
    result = check(s, w, now)
    return {"step": step + 1, "done": SCRIPT[step], "next": SCRIPT[step + 1] if step + 1 < len(SCRIPT) else None,
            "watch": w.id, "check": result, "message": f"已推进：{SCRIPT[step]}。"}


def _restore_seeded_memory(s: Session) -> int:
    """Put memory back to the seeded profile: facts recorded during the demo go, with their events and suppressions,
    and seeded facts that were superseded or forgotten become active again. Without this, a 10-24 query read as
    "at most 3 days a week" outlived every reset and the reports kept telling visitors they could do 3 days."""
    from polaris.db.models import MemoryEvent, MemorySuppression

    seeded = MemoryFact.source_text.startswith(DISCLOSURE)
    s.execute(MemoryEvent.__table__.delete())
    s.execute(MemorySuppression.__table__.delete())
    removed = s.execute(MemoryFact.__table__.delete().where(MemoryFact.source_text.is_(None) | ~seeded)).rowcount
    s.execute(MemoryFact.__table__.update().where(seeded).values(status="active", superseded_at=None))
    return removed


def reset_script(s: Session, now: datetime | None = None) -> dict[str, Any]:
    """剧本重来：把被改过的两个合成岗位恢复原样，删掉剧本新增的岗位、面经、样本和演示跟进卡。
    Memory and visitors' application marks go back to the seed too."""
    from polaris.db.models import JobFollowup, JobFollowupEvent, Watch

    if not get_settings().demo_mode:
        raise ValueError("演示剧本只能在合成演示库里运行")
    assert_ready(s)
    now = now or datetime.now(UTC)
    first, second = s.get(Job, 1), s.get(Job, 2)
    first.days_per_week = 4
    first.requirements = [r.replace("每周至少到岗5天", "每周至少到岗4天") for r in first.requirements]
    first.jd_text = (first.jd_text or "").replace("每周至少到岗5天", "每周至少到岗4天")
    second.status, second.last_seen_at = "active", now
    s.execute(Job.__table__.delete().where(Job.key == f"{VERSION}:job:new-1"))
    # 原文删了，挂在它下面的岗位挂牌、帖子、题目和同类样本按外键一起删
    removed = s.execute(RawItem.__table__.delete().where(RawItem.external_id.in_(
        [f"{VERSION}:job:new-1", f"{VERSION}:post:new-1"]))).rowcount
    s.execute(Run.__table__.delete().where(Run.kind == "demo-advance"))
    s.execute(Watch.__table__.delete().where(Watch.title == WATCH_TITLE))
    # The seed has no application history; whatever a visitor marked (applied, rejected, notes) goes too.
    s.execute(JobFollowupEvent.__table__.delete())
    followups = s.execute(JobFollowup.__table__.delete()).rowcount
    return {"reset": True, "removed_raw_items": removed, "removed_memory_facts": _restore_seeded_memory(s),
            "removed_followups": followups}


def script_state(s: Session) -> dict[str, Any]:
    from polaris.db.models import Watch

    step = s.scalar(select(func.count()).select_from(Run).where(Run.kind == "demo-advance")) or 0
    started = s.scalar(select(Watch.id).where(Watch.title == WATCH_TITLE)) is not None
    return {"available": True, "started": started, "step": step, "total": len(SCRIPT), "steps": SCRIPT,
            "next": None if step >= len(SCRIPT) else ("开一张演示跟进卡" if not started else SCRIPT[step])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true", help="向独立空演示库导入资料，不启动数据库或API")
    parser.add_argument("--apply", action="store_true", help="明确执行导入；无此开关仅检查内存样本")
    parser.add_argument("--advance", action="store_true", help="跟进卡演示剧本推进一步（只在演示库里）")
    args = parser.parse_args()
    if args.advance:
        settings = get_settings()
        require_demo_target(settings, get_engine())
        with session_scope() as s:
            print(json.dumps(advance(s), ensure_ascii=False, default=str))
        return
    if args.apply and not args.seed:
        parser.error("--apply须配合--seed")
    if not args.seed or not args.apply:
        print(json.dumps({"status": "offline_check", "fixture": VERSION, "counts": fixture(datetime.now(UTC)).counts,
                          "disclosure": DISCLOSURE, "database_accessed": False}, ensure_ascii=False))
        return
    settings = get_settings()
    print(json.dumps(seed(get_engine(), settings, datetime.now(UTC)), ensure_ascii=False))


if __name__ == "__main__":
    main()
