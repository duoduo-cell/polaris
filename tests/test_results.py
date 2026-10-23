"""来源卡片和岗位进度的业务回归，完全使用合成数据，无模型费用。"""
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from polaris.api.app import app
from polaris.db.models import InterviewQuestion, Job, JobFollowupEvent, Post, Posting, RawItem, SearchChunk
from polaris.db.session import session_scope
from polaris.followups import FollowupConflict, get, update
from polaris.graph.build import collect
from polaris.interviews import sync_questions
from polaris.memory.job_scope import resolve, sync_followup
from polaris.memory.schema import qualifier_key
from polaris.memory.update import Candidate, Context, apply, slot_facts
from polaris.results import entities, presentation
from polaris.search.index import sync_chunks, sync_post_chunks
from polaris.sources import safe_url
from polaris.tools.base import render

ME = {"Authorization": "Bearer test-owner-token"}
client = TestClient(app)
NOW = datetime(2026, 10, 12, tzinfo=UTC)


def seed():
    with session_scope() as s:
        jobs = [Job(key=f"synthetic-test:{i}", company="字节跳动", company_name="字节跳动", title=title,
                    job_type="intern", city="北京", jd_text="合成测试岗位要求", first_seen_at=NOW, last_seen_at=NOW)
                for i, title in enumerate(("Agent 开发实习", "RAG 算法实习", "后端研发实习"))]
        s.add_all(jobs)
        s.flush()
        for i, j in enumerate(jobs):
            raw = RawItem(source="xhs", tier="B", kind="post", content_hash=f"test-job-{i}",
                          raw_text="合成测试岗位", url=f"https://www.xiaohongshu.com/explore/test-{i}", raw_json={}, fetched_at=NOW)
            s.add(raw)
            s.flush()
            s.add(Posting(raw_item_id=raw.id, job_id=j.id, company_name=j.company, big_company=j.company, title=j.title))
        specs = [("字节 Agent 一面\n问：RAG 如何召回？\n二面\n问：如何设计记忆？\n评论：\n华为招聘实习生", "面经"),
                 ("10月 Agent 一面面经 八股文\n大厂面试特点：字节腾讯华为\nRAG 如何召回？", "面经"),
                 ("面经：一面问了 RAG 如何召回？", "面经")]
        posts = []
        for i, (text, kind) in enumerate(specs):
            raw = RawItem(source="xhs", tier="B", kind="post", content_hash=f"test-post-{i}", raw_text=text, raw_json={},
                          url=f"https://www.xiaohongshu.com/explore/post-{i}", published_at=NOW, fetched_at=NOW)
            s.add(raw)
            s.flush()
            p = Post(raw_item_id=raw.id, post_type=kind, companies=["字节跳动", "华为"], big_companies=["字节跳动", "华为"],
                     roles=["Agent 算法"], summary="合成测试资料", interview_questions=["RAG 如何召回？", "如何设计记忆？"],
                     published_at=NOW)
            s.add(p)
            s.flush()
            posts.append(p.id)
        return [j.id for j in jobs], posts


def test_online_chunk_sync_preserves_other_posts_jobs_and_skips_busy_posts(db):
    _, posts = seed()
    sync_chunks()
    with session_scope() as s:
        before = {c.key: (c.id, c.content_hash) for c in s.scalars(select(SearchChunk))}
        p = s.get(Post, posts[0])
        p.interview_questions = ["RAG 如何召回？"]
    with session_scope() as locked:
        locked.execute(select(Post).where(Post.id == posts[0]).with_for_update())
        with session_scope() as s:
            assert sync_post_chunks(s, [posts[0]])["skipped_posts"] == 1
    with session_scope() as s:
        stats = sync_post_chunks(s, [posts[0]])
        assert stats["posts"] == 1 and stats["questions"] == 1 and stats["removed"] == 1
        after = {c.key: (c.id, c.content_hash) for c in s.scalars(select(SearchChunk))}
        assert f"post:{posts[0]}:q1" not in after
        assert after == {k: v for k, v in before.items() if k != f"post:{posts[0]}:q1"}
        assert sync_post_chunks(s, [])["total"] == 0


def test_three_verified_recommendations_have_sources_and_hallucinations_do_not(db):
    ids, _ = seed()
    calls = [{"ok": True, "output": json.dumps({"items": [{"id": f"J-{i}"} for i in ids]})}]
    with session_scope() as s:
        cards = presentation(s, "推荐：" + "、".join(f"J-{i}" for i in ids) + "，J-99999", calls)
        assert len(cards) == 3
        assert all(r["sources"][0]["url"].startswith("https://www.xiaohongshu.com/") for r in cards)
        assert all("字节跳动" in r["display_title"] for r in cards)
        assert all(r["followup"]["status"] == "untracked" for r in cards)
        assert presentation(s, f"J-{ids[0]}", [{"ok": False, "output": calls[0]["output"]}]) == []


@pytest.mark.parametrize("label", ["未列出的第三条是", "未放入前两项：", "不推荐：", "仅供对照："])
def test_explicitly_unlisted_job_stays_linkable_without_an_extra_featured_card(db, label):
    ids, _ = seed()
    calls = [{"ok": True, "output": json.dumps({"items": [{"id": f"J-{i}"} for i in ids]})}]
    answer = f"推荐J-{ids[0]}、J-{ids[1]}。\n\n{label}J-{ids[2]}，偏后端。"
    with session_scope() as s:
        cards = presentation(s, answer, calls)
    assert len(cards) == 3  # 引用和详情证据仍在，不能把原文编号变成不可点击。
    assert [r["id"] for r in cards if r.get("show_card", True)] == [f"J-{i}" for i in ids[:2]]
    assert cards[2]["sources"] and cards[2]["show_card"] is False


@pytest.mark.parametrize("text", ["不是不推荐J-{i}", "推荐J-{i}", "对照J-{i}与J-{j}",
                                 "未列出J-{i}\n\n后来改为推荐J-{i}", "不推荐J-{i}，但推荐J-{j}"])
def test_ambiguous_or_later_recommended_mentions_are_not_hidden(db, text):
    ids, _ = seed()
    answer = text.format(i=ids[0], j=ids[1])
    calls = [{"ok": True, "output": json.dumps({"items": [{"id": f"J-{i}"} for i in ids]})}]
    with session_scope() as s:
        cards = presentation(s, answer, calls)
    assert all(r.get("show_card", True) for r in cards)


def test_unlisted_card_flag_survives_reconnect_and_does_not_shift_job_ordinals(db):
    from polaris.agent.tasks import snapshot
    from polaris.db.models import ChatMessage, ChatThread
    from polaris.memory.job_scope import recent_jobs

    ids, _ = seed()
    calls = [{"ok": True, "output": json.dumps({"items": [{"id": f"J-{i}"} for i in ids]})}]
    answer = f"推荐J-{ids[0]}和J-{ids[1]}\n\n未列出的第三条：J-{ids[2]}"
    with session_scope() as s:
        cards = presentation(s, answer, calls)
        s.add(ChatThread(id="unlisted-result", history_imported=True))
        s.flush()
        s.add(ChatMessage(thread_id="unlisted-result", source_key="answer", role="assistant",
                          content=answer, turn={"answer": answer, "results": cards}))
    result = snapshot("unlisted-result")["messages"][-1]["turn"]["results"]
    assert result[-1]["id"] == f"J-{ids[2]}" and result[-1]["show_card"] is False
    with session_scope() as s:
        assert [j.id for j in recent_jobs(s, "unlisted-result")] == ids[:2]


def test_refs_in_stats_examples_trend_and_sources_count_but_not_refs_inside_text():
    """10-12 回答评测：kg_stats 的例子、jobs_trend 的新岗位、来源的原始记录里的编号以前不认，回答里引用了也点不开。"""
    from polaris.results import refs_from_output

    stats = {"groups": [{"group": "北京", "count": 2, "examples": ["J-7", "J-8"]}]}
    trend = {"examples_new": ["J-9"], "caveats": ["快照才 1 天"]}
    job = {"id": "J-1", "sources": [{"raw_id": "R-5", "url": "https://example.com"}]}
    post = {"items": [{"id": "P-2", "summary": "帖子里写了 J-404 和 Q-405", "text": "J-406"}]}
    assert refs_from_output(json.dumps(stats)) == {"J-7", "J-8"}
    assert refs_from_output(json.dumps(trend)) == {"J-9"}
    assert refs_from_output(json.dumps(job)) == {"J-1", "R-5"}
    assert refs_from_output(json.dumps(post)) == {"P-2"}
    assert refs_from_output('{"ref": "J-3", "examples": ["J-4"') == {"J-3"}  # 截断的 JSON 只认字段


def test_comment_company_study_and_rounds_are_separated_in_index_and_graph(db):
    _, posts = seed()
    sync_chunks()
    with session_scope() as s:
        first = s.scalars(select(InterviewQuestion).where(InterviewQuestion.post_id == posts[0]).order_by(InterviewQuestion.key)).all()
        assert [q.company for q in first] == ["字节跳动", "字节跳动"]
        assert [q.round for q in first] == ["1面", "2面"]
        assert first[0].evidence == "问：RAG 如何召回？"
        study = s.scalars(select(InterviewQuestion).where(InterviewQuestion.post_id == posts[1])).all()
        assert all(q.company is None and q.material == "study" for q in study)
        chunks = s.scalars(select(SearchChunk).where(SearchChunk.post_id == posts[0], SearchChunk.kind == "question")).all()
        assert all(c.companies == ["字节跳动"] for c in chunks)
        detail = entities(s, [f"P-{posts[0]}"], detailed=True)[0]
        assert detail["comment_companies"] == ["华为"] and "华为招聘" not in detail["text"]
        ids = [f"Q-{q.id}" for q in first]
    assert all(r["company"] == "字节跳动" for r in collect()["question_company"])
    # 帖子详情里每道题带真实题号（10-12：以前明细被输出上限裁掉，模型只能顺着编号推）
    from polaris.results import refs_from_output
    from polaris.tools import REGISTRY
    from polaris.tools.runtime import ToolRuntime

    out = ToolRuntime(record_db=False).invoke(REGISTRY["posts_get"], {"post_id": f"P-{posts[0]}"})
    got = json.loads(out)["questions"]
    assert [q["id"] for q in got] == ids and got[0]["question"] == "RAG 如何召回？" and got[0]["round"] == "1面"
    assert set(ids) <= refs_from_output(out)


def test_backfill_is_idempotent_and_preserves_question_ids(db):
    seed()
    with session_scope() as s:
        sync_questions(s)
        before = dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.id)).all())
        sync_questions(s)
        assert dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.id)).all()) == before


def test_scoped_question_sync_preserves_other_posts_and_question_ids(db):
    _, posts = seed()
    with session_scope() as s:
        sync_questions(s)
        before = dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.id)).all())
        s.get(Post, posts[0]).interview_questions = ["RAG 如何召回？"]
        stats = sync_questions(s, [posts[0]])
        assert stats["posts"] == 1 and stats["questions"] == 1
        after = dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.id)).all())
        assert after[f"post:{posts[0]}:q0"] == before[f"post:{posts[0]}:q0"]
        assert f"post:{posts[0]}:q1" not in after
        assert all(after[k] == v for k, v in before.items() if not k.startswith(f"post:{posts[0]}:"))
        assert sync_questions(s, [])["posts"] == 0
        assert dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.id)).all()) == after


def test_online_question_sync_skips_a_post_locked_by_extraction(db):
    _, posts = seed()
    with session_scope() as held:
        held.execute(select(Post).where(Post.id == posts[0]).with_for_update()).one()
        with session_scope() as s:
            stats = sync_questions(s, [posts[0]], skip_locked=True)
            assert stats["posts"] == 0 and stats["skipped_posts"] == 1


def test_two_jobs_do_not_share_progress_or_bookmarks_and_retry_does_not_roll_back(db):
    ids, _ = seed()
    key = str(uuid4())
    with session_scope() as s:
        update(s, ids[0], {"saved": True}, key, "收藏", 0)
        assert get(s, ids[0])["status"] == "untracked"
        update(s, ids[1], {"status": "applied"}, str(uuid4()), "明确投递", 0)
        update(s, ids[0], {"status": "interview", "stage": "一面通过"}, str(uuid4()), "面试", 1)
        assert update(s, ids[0], {"saved": True}, key, "重试", 0)["stage"] == "一面通过"
        assert get(s, ids[1])["stage"] is None
        assert len(s.scalars(select(JobFollowupEvent)).all()) == 3
        with pytest.raises(FollowupConflict):
            update(s, ids[0], {"status": "offer"}, str(uuid4()), "过期页面", 0)
        with pytest.raises(FollowupConflict):
            update(s, ids[1], {"saved": True}, key, "错误复用")


def test_progress_memory_is_scoped_to_job_and_ambiguity_asks(db):
    ids, _ = seed()
    with session_scope() as s:
        for jid, stage in zip(ids[:2], ("一面通过", "准备二面"), strict=True):
            c = Candidate("interview_stage", stage, f"用户{stage}", qualifier="字节")
            j, question = resolve(s, c, f"J-{jid} {stage}", [])
            assert j and not question
            event, _ = apply(Context(s, NOW, f"J-{jid} {stage}"), c)
            sync_followup(s, j, c, event, f"J-{jid} {stage}")
        assert slot_facts(s, "interview_stage", f"字节 | J-{ids[0]}")[0].value == "一面通过"
        assert slot_facts(s, "interview_stage", f"字节 | J-{ids[1]}")[0].value == "准备二面"
        _, question = resolve(s, Candidate("interview_stage", "三面通过", "用户三面通过", qualifier="字节"), "字节三面通过", [])
        assert "哪个岗位" in question
    assert qualifier_key(f"ByteDance | J-{ids[0]}") == f"字节跳动 | J-{ids[0]}"


def test_inferred_application_does_not_mark_job_as_applied(db):
    ids, _ = seed()
    with session_scope() as s:
        c = Candidate("applied", "投递过", "推断用户投过", kind="inferred", qualifier="字节")
        job, _ = resolve(s, c, f"J-{ids[0]} 我在看面经", [])
        event, _ = apply(Context(s, NOW, "我在看面经"), c)
        sync_followup(s, job, c, event, "我在看面经")
        assert get(s, ids[0])["status"] == "untracked"


def test_browse_filters_and_explicit_followup_api(db):
    ids, _ = seed()
    sync_chunks()
    assert client.get(f"/api/me/results/J-{ids[0]}").status_code == 401
    out = client.get("/api/me/knowledge/questions", params={"company": "华为", "material": "firsthand"}, headers=ME).json()
    assert out["items"] == []
    out = client.get("/api/me/knowledge/questions", params={"company": "字节", "round": "2面", "material": "firsthand"}, headers=ME).json()
    assert len(out["items"]) == 1 and out["items"][0]["sources"][0]["url"]
    out = client.get("/api/me/knowledge/posts", params={"material": "study"}, headers=ME).json()
    assert len(out["items"]) == 1 and out["items"][0]["kind"] == "study"
    body = {"request_id": str(uuid4()), "revision": 0, "changes": {"saved": True}}
    assert client.post(f"/api/me/jobs/J-{ids[0]}/followup", json=body, headers=ME).json()["status"] == "untracked"
    assert client.post(f"/api/me/jobs/J-{ids[0]}/followup", json=body, headers=ME).status_code == 200
    body["request_id"] = str(uuid4())
    assert client.post(f"/api/me/jobs/J-{ids[0]}/followup", json=body, headers=ME).status_code == 409


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:secret@example.com/", "file:///etc/passwd", "https://x.com/\nfoo"])
def test_source_urls_only_allow_safe_http(url):
    assert safe_url(url) is None


def test_large_tool_result_is_valid_json_and_preserves_paging():
    payload = {"items": [{"id": f"J-{i}", "title": "合成" * 300, "sources": [{"url": f"https://example.com/jobs/{i}"}]}
                         for i in range(20)], "returned": 20, "next_offset": 40, "offset": 20}
    out = json.loads(render(payload))
    assert out["truncated"] and out["next_offset"] == 20 + len(out["items"])
    assert out["items"][0]["sources"][0]["url"] == "https://example.com/jobs/0"
    assert len(payload["items"]) == 20


def test_single_oversized_result_and_url_are_not_cut_into_invalid_json():
    text = render({"id": "J-7", "display_title": "标题" * 4000,
                   "sources": [{"url": "https://example.com/" + "x" * 8000}], "jd_text": "正文" * 10000})
    out = json.loads(text)
    assert len(text) <= 6000 and out["id"] == "J-7"
    assert out["sources"][0]["url_in_detail"] and "url" not in out["sources"][0]


def test_tool_cards_are_persisted_in_actual_chat_transcript(db):
    from typing import Any

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from polaris.agent.main import chat
    from polaris.agent.transcript import visible_messages

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any):
            return self

    ids, _ = seed()
    answer = "推荐：" + "、".join(f"J-{i}" for i in ids)
    model = Model(messages=iter([AIMessage(content="", tool_calls=[
        {"name": "jobs_search", "args": {"limit": 3, "big_only": False}, "id": "c1"}]),
                                 AIMessage(content=answer)]))
    turn = chat("推荐三个岗位", "card-test", model=model, remember_fn=lambda *a, **k: {"events": [], "questions": []})
    assert len(turn.results) == 3 and turn.activities[0]["sources"] == ["小红书"]
    with session_scope() as s:
        messages = visible_messages(s, "card-test")
        assert messages[-1]["turn"]["results"] == turn.results
        assert messages[0]["content"] == "推荐三个岗位"
        update(s, ids[0], {"saved": True, "status": "applied"}, str(uuid4()), "用户保存状态")
        refreshed = visible_messages(s, "card-test")
        cards = {r["id"]: r for r in refreshed[-1]["turn"]["results"]}
        assert cards[f"J-{ids[0]}"]["followup"]["saved"]
        assert cards[f"J-{ids[0]}"]["followup"]["status"] == "applied"
        assert cards[f"J-{ids[1]}"]["followup"]["status"] == "untracked"
        assert refreshed[-1]["content"] == answer


def test_company_level_progress_is_kept_while_asking_which_job(db):
    """只说了公司、没说哪个岗位：先记在公司这一层并追问，不能因为用户没回答追问就整条丢掉（10-12 探针）。"""
    from types import SimpleNamespace

    from polaris.memory.extract import Extraction, FactCandidate, remember

    seed()
    facts = [FactCandidate(attribute="interview_stage", qualifier="字节", value="一面已过，等二面",
                           statement="用户字节一面已过，在等二面", kind="stated")]
    llm = SimpleNamespace(extract=lambda *a, **k: SimpleNamespace(ok=True, value=Extraction(facts=facts), error=None))
    out = remember("我投过字节的 Agent 研发实习，现在一面过了在等二面", llm=llm, now=NOW)
    assert any("哪个岗位" in q for q in out["questions"])
    assert out["events"][0]["scope_label"] == "字节跳动（具体岗位待确认）"
    with session_scope() as s:
        assert slot_facts(s, "interview_stage", "字节跳动")[0].value == "一面已过，等二面"
        assert s.scalars(select(JobFollowupEvent)).all() == []  # 不猜具体岗位，岗位进度不动
