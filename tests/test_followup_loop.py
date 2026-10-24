"""跟进环路：起草跟进消息（只起草不发）、被拒理由写回卡片、投递后一周没动静提醒。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from polaris.db.models import Job, JobFollowup, MemoryFact
from polaris.db.session import session_scope
from polaris.followups import draft, stale, update
from polaris.memory.update import Candidate, Context, apply


def seed(days_per_week=None):
    now = datetime.now(UTC)
    with session_scope() as s:
        apply(Context(s, now, "合成用户背景"), Candidate("school", "合成大学", "我在合成大学"))
        apply(Context(s, now, "合成用户背景"), Candidate("days_per_week", "4", "每周能去 4 天"))
        row = Job(key=f"followup-{uuid4()}", company_name="合成公司", title="合成Agent实习", city="北京", cities=["北京"],
                  job_type="intern", status="active", directions=["Agent"], days_per_week=days_per_week,
                  requirements=["熟悉 Python", "了解 RAG"], first_seen_at=now, last_seen_at=now, is_synthetic=False)
        s.add(row)
        s.flush()
        return row.id


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        return SimpleNamespace(content=" 您好，想问一下简历进度…… ", call_id=7)


def test_draft_uses_progress_card_and_unknown_jd_fields_without_sending(db):
    jid = seed()
    with session_scope() as s:
        update(s, jid, {"status": "applied"}, str(uuid4()), "合成操作")
    llm = FakeLLM()
    with session_scope() as s:
        out = draft(s, jid, llm=llm)
    assert out["text"] == "您好，想问一下简历进度……" and out["status"] == "applied"
    assert "没写每周几天" in out["asks"]  # JD 没写天数：顺带问一句
    prompt = llm.prompts[0]
    assert "已投递" in prompt and "合成大学" in prompt and "每周能到岗几天：4" in prompt
    assert "[作品链接]" in prompt  # 没配 PORTFOLIO_URL 时让用户自己填，不编
    assert out["missing"] == ["能实习多久（月）", "最早什么时候能开始"]  # 卡片没有的到岗信息：占位，不能写“满足”
    assert "卡片里没有：能实习多久（月）、最早什么时候能开始" in prompt
    with session_scope() as s:  # 起草不改进度
        assert s.scalar(select(JobFollowup.status).where(JobFollowup.job_id == jid)) == "applied"


def test_rejection_reason_goes_into_card_once(db):
    jid = seed(days_per_week=4)
    with session_scope() as s:
        out = update(s, jid, {"status": "rejected", "note": "项目深度不够，算法题没写出来"}, str(uuid4()), "合成操作")
    assert out["memory_event"]["op"] == "add" and out["memory_event"]["label"] == "被拒的反馈"
    with session_scope() as s:
        out = update(s, jid, {"stage": "已复盘"}, str(uuid4()), "合成操作")  # 只改进度不改理由：不重复写
        assert "memory_event" not in out
        facts = s.scalars(select(MemoryFact).where(MemoryFact.attribute == "rejection_feedback")).all()
    assert len(facts) == 1 and facts[0].qualifier == "合成公司 | 合成Agent实习"
    assert facts[0].value == "项目深度不够，算法题没写出来"


def test_applied_without_news_for_a_week_is_reminded_unless_user_set_a_date(db):
    jid = seed()
    with session_scope() as s:
        update(s, jid, {"status": "applied"}, str(uuid4()), "合成操作")
        row = s.scalar(select(JobFollowup).where(JobFollowup.job_id == jid))
        row.updated_at = datetime.now(UTC) - timedelta(days=8)
    with session_scope() as s:
        assert [(j.id, days) for _, j, days in stale(s)] == [(jid, 8)]
        s.scalar(select(JobFollowup).where(JobFollowup.job_id == jid)).next_followup = datetime.now(UTC).date()
    with session_scope() as s:
        assert stale(s) == []  # 自己定了跟进日期：按你定的日子提醒，不重复


def test_funnel_counts_furthest_step_each_job_reached(db):
    from polaris.followups import funnel

    a, b, c = seed(), seed(), seed()
    with session_scope() as s:
        update(s, a, {"saved": True}, str(uuid4()), "合成操作")
        for job, steps in ((a, ["applied", "interview", "rejected"]), (b, ["applied"]), (c, ["applied", "interview", "offer"])):
            for status in steps:
                update(s, job, {"status": status}, str(uuid4()), "合成操作")
    with session_scope() as s:
        out = funnel(s)
    assert {x["key"]: x["count"] for x in out["steps"]} == {"saved": 1, "applied": 3, "replied": 2, "interview": 2, "offer": 1}
    assert out["rates"] == {"reply": 0.667, "interview": 0.667, "offer": 0.333}
    assert sum(w["applied"] for w in out["weeks"]) == 3
