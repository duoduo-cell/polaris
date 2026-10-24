"""本次上海/三天与长期北京/五天不同：真实工具和历史卡片必须采用本次条件。"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from polaris.agent.main import chat
from polaris.agent.transcript import visible_messages
from polaris.db.models import Job, MemoryFact
from polaris.db.session import session_scope
from polaris.memory.update import Candidate, Context, apply
from polaris.results import presentation
from polaris.tools import REGISTRY
from polaris.tools.runtime import ToolRuntime


def seed():
    now = datetime.now(UTC)
    with session_scope() as s:
        for attr, value in [("target_city", "北京"), ("days_per_week", "5"), ("degree", "本科在读")]:
            apply(Context(s, now, "合成用户长期条件"), Candidate(attr, value, f"合成用户{value}"))
        row = Job(key="request-matching", company_name="合成公司", title="合成Agent工程实习", city="上海", cities=["上海"],
                  days_per_week=3, job_type="intern", status="active", directions=["Agent"],
                  requirements=["本科及以上在读"], first_seen_at=now, last_seen_at=now, is_synthetic=False)
        s.add(row)
        s.flush()
        return row.id


def check(data, key):
    return next(row for row in data["match"]["checks"] if row["key"] == key)


def test_current_search_details_and_cards_do_not_revert_to_long_term_city(db):
    jid = seed()
    runtime = ToolRuntime(agent="main", record_db=False)
    runtime.user_request = "这次只看上海的实习，每周最多3天，不改长期偏好。"
    output = json.loads(runtime.invoke(REGISTRY["jobs_search"], {
        "city": "上海", "job_type": "intern", "max_days_per_week": 3, "big_only": False,
    }))
    assert output["returned"] == 1
    item = output["items"][0]
    assert check(item, "city")["ok"] is True
    assert check(item, "days")["detail"] == "每周 3 天"
    assert item["match"]["eligibility"] != "incompatible"
    assert item["match"]["criteria"] == {"city": "上海", "job_type": "intern", "max_days_per_week": 3}
    detail = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{jid}"}))
    # 列表只留核对结果，逐条门槛解释和固定说明在详情里；其余与详情一致
    assert {k: v for k, v in detail["match"].items() if k not in ("requirements_review", "note")} == item["match"]
    assert output["match_note"] == detail["match"]["note"]
    with session_scope() as s:
        card = presentation(s, f"本次可看 J-{jid}，出勤已写明。", runtime.calls)[0]
        assert card["match"] == detail["match"]  # 卡片按同一条件补回逐条门槛解释
        assert {(f.attribute, f.value) for f in s.scalars(select(MemoryFact))} == {
            ("target_city", "北京"), ("days_per_week", "5"), ("degree", "本科在读")}


def test_query_override_preserves_degree_gate_and_unknown_days(db):
    from polaris.match import matches

    jid = seed()
    with session_scope() as s:
        row = s.get(Job, jid)
        row.requirements = ["正在攻读博士学位。"]
        row.days_per_week = None
        out = matches(s, [jid], criteria={"city": "上海", "max_days_per_week": 3})[jid]
        data = {"match": out}
        assert check(data, "city")["ok"] is True
        assert check(data, "degree")["ok"] is False
        assert check(data, "days")["ok"] is None
        assert out["eligibility"] == "incompatible"
        row.requirements = ["本科及以上在读"]
        row.days_per_week = 4
        out = matches(s, [jid], criteria={"city": "上海", "max_days_per_week": 3})[jid]
        assert check({"match": out}, "days")["ok"] is False
        assert "你能去 3 天" in check({"match": out}, "days")["detail"]


def test_parallel_queries_use_separate_scopes_without_mutating_profile(db):
    shanghai = seed()
    now = datetime.now(UTC)
    with session_scope() as s:
        row = Job(key="request-beijing", company_name="合成公司", title="合成北京岗位", city="北京", cities=["北京"],
                  days_per_week=2, status="active", first_seen_at=now, last_seen_at=now, is_synthetic=False)
        s.add(row)
        s.flush()
        beijing = row.id
    runtime = ToolRuntime(agent="main", record_db=False)
    def query(city):
        return json.loads(runtime.invoke(REGISTRY["jobs_search"], {"city": city, "big_only": False}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(query, ["上海", "北京"]))
    assert check(answers[0]["items"][0], "city")["ok"] is True
    assert check(answers[1]["items"][0], "city")["ok"] is True
    for jid, city in [(shanghai, "上海"), (beijing, "北京")]:
        detail = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{jid}"}))
        assert detail["match"]["criteria"]["city"] == city
    with session_scope() as s:
        cards = presentation(s, f"J-{shanghai} 与 J-{beijing}", runtime.calls)
        assert {c["match"]["criteria"]["city"] for c in cards} == {"上海", "北京"}


class Model(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_chat_history_and_later_details_reuse_scope_without_changing_long_term_memory(db):
    jid = seed()
    tid = f"request-scope-{uuid4()}"
    model = Model(messages=iter([
        AIMessage(content="", tool_calls=[{"id": "search", "name": "jobs_search", "args": {
            "city": "上海", "max_days_per_week": 3, "big_only": False}}]),
        AIMessage(content=f"本次上海岗位 J-{jid}，没有修改长期偏好。"),
    ]))
    turn = chat("这次只查上海，每周最多3天，不改长期偏好。", tid, model=model,
                remember_fn=lambda *a, **kw: {"events": [], "questions": []})
    assert turn.error is None and check(turn.results[0], "city")["ok"] is True
    with session_scope() as s:
        before = visible_messages(s, tid)[-1]["turn"]["results"][0]["match"]
        apply(Context(s, datetime.now(UTC), "合成用户更换长期城市"), Candidate("target_city", "杭州", "合成用户长期改为杭州"))
    with session_scope() as s:
        after = visible_messages(s, tid)[-1]["turn"]["results"][0]["match"]
        assert before == after
    runtime = ToolRuntime(agent="main", thread_id=tid, record_db=False)
    detail = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{jid}"}))
    assert detail["match"]["criteria"] == {"city": "上海", "max_days_per_week": 3}
    assert check(detail, "city")["ok"] is True
    # 本轮重新不带城市查询，明确回到长期画像，不能暗用更早一轮的上海。
    global_result = json.loads(runtime.invoke(REGISTRY["jobs_search"], {"big_only": False}))
    assert check(global_result["items"][0], "city")["ok"] is False
    refreshed = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{jid}"}))
    assert "criteria" not in refreshed["match"] and check(refreshed, "city")["ok"] is False


def test_unknown_days_stay_unknown_and_failed_query_does_not_leak_scope(db):
    jid = seed()
    with session_scope() as s:
        s.get(Job, jid).days_per_week = None
    runtime = ToolRuntime(agent="main", record_db=False, faults={"jobs_search": ["timeout"]})
    runtime.invoke(REGISTRY["jobs_search"], {"city": "上海", "big_only": False})
    detail = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{jid}"}))
    assert "criteria" not in detail["match"] and check(detail, "city")["ok"] is False
    out = json.loads(runtime.invoke(REGISTRY["jobs_search"], {"city": "上海", "big_only": False}))
    assert check(out["items"][0], "days")["ok"] is None


def test_invalid_scope_values_cannot_replace_long_term_profile():
    from polaris.matching_scope import criteria, scoped_profile
    from polaris.proactive.decide import Profile

    original = Profile(cities=["北京"], days_per_week=5)
    assert criteria({"city": [], "job_type": "something", "max_days_per_week": True}) == {}
    changed = scoped_profile(original, criteria({"city": "上海", "max_days_per_week": 3, "school": "随意数据"}))
    assert changed.cities == ["上海"] and changed.days_per_week == 3
    assert original.cities == ["北京"] and original.days_per_week == 5


def test_same_job_requeried_under_another_scope_does_not_use_old_details_cache(db):
    jid = seed()
    with session_scope() as s:
        job = s.get(Job, jid)
        job.city, job.cities = "上海、北京", ["上海", "北京"]
    runtime = ToolRuntime(agent="main", record_db=False)
    for city in ["上海", "北京"]:
        runtime.invoke(REGISTRY["jobs_search"], {"city": city, "big_only": False})
        detail = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{jid}"}))
        assert detail["match"]["criteria"]["city"] == city
        assert check(detail, "city")["detail"] == city
    assert sum(call["tool"] == "jobs_get" for call in runtime.calls) == 2
    # 详情工具原本接受纯数字和小写短号，范围关联不能只支持一种拼写。
    for reference in [str(jid), f" j-{jid} "]:
        detail = json.loads(runtime.invoke(REGISTRY["jobs_get"], {"job_id": reference}))
        assert detail["match"]["criteria"]["city"] == "北京"
