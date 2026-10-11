"""网页对话的过程展示：步骤说成人话、查到多少、回答草稿、上下文压缩、停止回答，以及岗位和你条件的匹配。
全部用假模型和测试库，不调真实服务。"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from polaris.agent import main
from polaris.agent.main import STOPPED, Cancelled, LiveHandler
from polaris.agent.steps import describe, outcome
from polaris.agent.tasks import TaskService, snapshot, submit
from polaris.api.app import app
from polaris.db.models import ChatTask, Job
from polaris.db.session import session_scope
from polaris.match import job_match, profile
from polaris.proactive.decide import Profile
from test_main_agent import call, script

AUTH = {"Authorization": "Bearer test-owner-token"}
NO_MEMORY = {"remember_fn": lambda *a, **k: {"events": [], "questions": []}}


def tid():
    return f"t-{uuid4().hex}"


def test_steps_read_like_sentences_and_never_show_raw_ids(db):
    assert describe("jobs_search", {"city": "北京", "job_type": "intern", "query": "Agent 开发", "limit": 5}) == \
        "搜岗位 · 北京 · 实习 · “Agent 开发”"
    assert describe("kg_stats", {"of": "jobs", "group_by": "company", "skill": "RAG"}) == "查知识图谱 · 数岗位，按公司分 · RAG"
    assert describe("jobs_get", {"job_id": "J-999"}) == "看岗位详情"  # 查不到名字就不写，也不露编号
    assert describe("memory_forget", {"attribute": "school"}) == "忘掉一条记忆 · 学校"
    assert outcome("jobs_search", {"returned": 3, "items": [], "next_offset": 3}, True) == "找到 3 个岗位，还有更多"
    assert outcome("posts_search", {"returned": 0, "items": []}, True) == "没找到符合条件的"
    assert outcome("kg_stats", {"matched": 12, "groups": [{}, {}]}, True) == "符合条件的有 12 个，分成 2 组"
    assert outcome("jobs_search", None, False, "出错了：来源暂时限流（HTTP 429）。\n怎么改：……") == "来源暂时限流（HTTP 429）。"


def _job(**kw):
    base = {"title": "Agent 开发实习生", "company": "字节跳动", "company_name": "字节跳动", "city": "北京", "cities": ["北京"],
            "job_type": "intern", "days_per_week": 4, "duration_months": 3, "directions": ["Agent"],
            "requirements": ["熟悉 RAG 和 LangGraph", "了解 CUDA 算子优化"], "jd_text": ""}
    return Job(**{**base, **kw})


PROFILE = Profile(roles=["Agent 开发"], cities=["北京"], job_type="日常实习", days_per_week=4, duration_months=3,
                  skills=["RAG", "LangGraph"])


def test_match_lists_each_condition_and_skill_gap():
    m = job_match(_job(), PROFILE)
    checks = {c["key"]: (c["ok"], c["detail"]) for c in m["checks"]}
    assert checks == {"city": (True, "北京"), "type": (True, "实习"), "days": (True, "每周 4 天"),
                      "months": (True, "3 个月"), "role": (True, "Agent 开发")}
    assert m["skills"]["have"] == ["RAG/检索增强", "LangGraph"] and m["skills"]["missing"] == ["CUDA/算子"]
    assert m["score"] >= 80 and m["level"] == "good"


def test_match_hard_misses_cap_the_score_and_unknowns_stay_unknown():
    m = job_match(_job(city="上海", cities=["上海"], days_per_week=5, duration_months=None), PROFILE)
    checks = {c["key"]: (c["ok"], c["detail"]) for c in m["checks"]}
    assert checks["city"] == (False, "在上海") and checks["days"] == (False, "要每周 5 天，你能去 4 天")
    assert checks["months"] == (None, "没写实习多久")
    assert m["score"] <= 59 and m["level"] == "weak"
    avoid = job_match(_job(), Profile(companies_avoid=["字节"]))
    assert avoid["checks"][0] == {"key": "avoid", "label": "公司", "ok": False, "detail": "你说过不想去字节跳动"}
    unknown = job_match(_job(), Profile(cities=["北京"]))
    assert unknown["skills"]["missing"] == [] and unknown["skills"]["known"] is False  # 不知道你会什么，就不说你缺什么


def test_no_profile_means_no_match(db):
    assert profile() is None


def test_live_handler_previews_answers_only_and_stops_on_request():
    events, stop = [], {"now": False}
    h = LiveHandler(events.append, lambda: stop["now"])
    h.on_chat_model_start({}, [], run_id="answer", metadata={})
    h.on_llm_new_token("北京", chunk=ChatGenerationChunk(message=AIMessageChunk(content="北京")), run_id="answer")
    assert events == [{"type": "draft", "text": "北京"}]
    tool_chunk = AIMessageChunk(content="", tool_call_chunks=[{"name": "jobs_search", "args": "", "id": "c", "index": 0}])
    h.on_llm_new_token("", chunk=ChatGenerationChunk(message=tool_chunk), run_id="tools")
    h.on_llm_new_token("我先查", chunk=ChatGenerationChunk(message=AIMessageChunk(content="我先查")), run_id="tools")
    assert len(events) == 1  # 要调工具的那一步不预览
    h.on_chat_model_start({}, [], run_id="summary", metadata={"lc_source": "summarization"})
    assert events[-1] == {"type": "compacting"}
    stop["now"] = True
    h.on_llm_new_token("摘要", chunk=ChatGenerationChunk(message=AIMessageChunk(content="摘要")), run_id="summary")  # 摘要不打断
    with pytest.raises(Cancelled):
        h.on_llm_new_token("。", chunk=ChatGenerationChunk(message=AIMessageChunk(content="。")), run_id="answer")


def test_stop_between_steps_closes_pending_tool_calls_so_next_message_works(db):
    thread, events = tid(), []
    turn = main.chat("帮我找北京的岗位", thread, on_event=events.append,
                     should_stop=lambda: any(e["type"] == "tool" for e in events),
                     model=script(call("jobs_search", {"city": "北京"})), **NO_MEMORY)
    assert turn.error == STOPPED and turn.answer == ""
    assert [e["desc"] for e in events if e["type"] == "tool"] == ["搜岗位 · 北京"]
    saved = main.checkpointer().get_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
    last = saved.checkpoint["channel_values"]["messages"][-1]
    assert last.type == "tool" and "停止" in last.content
    again = main.chat("那算了，换个问题", thread, model=script(AIMessage(content="好的")), **NO_MEMORY)
    assert again.error is None and again.answer == "好的"


def test_tool_results_are_reported_and_turn_knows_context_size(db):
    events = []
    turn = main.chat("看看你记着什么", tid(), on_event=events.append,
                     model=script(call("memory_card", {}), AIMessage(content="还没记住什么")), **NO_MEMORY)
    done = [e for e in events if e["type"] == "tool_done"]
    assert done and done[0]["tool"] == "memory_card" and done[0]["summary"] == "还没记住什么" and done[0]["ok"] is True
    assert turn.context["limit"] == main.SUMMARY_TRIGGER_TOKENS and turn.context["tokens"] > 0
    assert "context" in turn.to_dict() and "compacted" not in turn.to_dict()


def test_compaction_is_reported_with_a_chinese_summary(db, monkeypatch):
    monkeypatch.setattr(main, "SUMMARY_TRIGGER_TOKENS", 40)
    monkeypatch.setattr(main, "SUMMARY_KEEP_MESSAGES", 2)
    thread = tid()
    main.chat("我想找北京的 Agent 开发实习，" * 12, thread, model=script(AIMessage(content="好的")), **NO_MEMORY)
    events = []
    turn = main.chat("第二句", thread, on_event=events.append,
                     model=script(AIMessage(content="摘要：用户在找北京的实习"), AIMessage(content="第二条回答")), **NO_MEMORY)
    compact = [e for e in events if e["type"] == "compact"]
    assert compact and compact[0]["summary"] == "摘要：用户在找北京的实习" and compact[0]["messages"] >= 1
    assert compact[0]["before_tokens"] > compact[0]["after_tokens"] > 0
    assert turn.answer == "第二条回答" and turn.compacted["summary"] == "摘要：用户在找北京的实习"
    assert [m["content"] for m in snapshot(thread)["messages"]][-1] == "第二条回答"  # 聊天记录照旧，不被摘要替换


def test_task_live_state_keeps_drafts_out_of_the_activity_log(db):
    task = submit(tid(), "chat", {"message": "在吗"}, str(uuid4()), start=False)
    with session_scope() as s:
        s.get(ChatTask, task["id"]).state = "running"
    worker = SimpleNamespace(engine=None)
    for e in [{"type": "draft", "text": "北京有"}, {"type": "compacting"},
              {"type": "tool", "tool": "jobs_search", "args": {}, "desc": "搜岗位"},
              {"type": "tool_done", "tool": "jobs_search", "ok": True, "returned": 2, "summary": "找到 2 个岗位"},
              {"type": "token", "text": "最终回答不进活动"}]:
        TaskService._event(worker, task["id"], e)
    with session_scope() as s:
        row = s.get(ChatTask, task["id"])
        assert [a["type"] for a in row.activities] == ["tool", "tool_done"]
        assert row.live["draft"] == "" and row.live["status"] is None and row.live["seq"] == 3


def test_stop_endpoint_cancels_queued_and_flags_running(db):
    client = TestClient(app)
    thread = tid()
    queued = submit(thread, "chat", {"message": "还没开始的问题"}, str(uuid4()), start=False)
    out = client.post(f"/api/me/chat/tasks/{queued['id']}/cancel", headers=AUTH).json()
    assert out["state"] == "cancelled" and "原话已保留" in out["error"]
    messages = snapshot(thread)["messages"]
    assert messages[0]["content"] == "还没开始的问题" and messages[0]["task_id"] == queued["id"]
    running = submit(tid(), "chat", {"message": "正在回答的问题"}, str(uuid4()), start=False)
    with session_scope() as s:
        s.get(ChatTask, running["id"]).state = "running"
    flagged = client.post(f"/api/me/chat/tasks/{running['id']}/cancel", headers=AUTH).json()
    assert flagged["state"] == "running" and flagged["cancel_requested"] is True
    assert client.post("/api/me/chat/tasks/missing/cancel", headers=AUTH).status_code == 404
    assert client.post(f"/api/me/chat/tasks/{running['id']}/cancel").status_code == 401


def test_agenda_only_reports_recorded_facts(db):
    client = TestClient(app)
    out = client.get("/api/me/agenda", headers=AUTH).json()
    assert out["doing"] == [] and out["done"] == []
    assert out["needs_you"][0]["kind"] == "profile"  # 还不知道你想找什么，先请你说
