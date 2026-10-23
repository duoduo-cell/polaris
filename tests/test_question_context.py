"""题目级空轮次不能抹掉原帖标题的证据；展示层不补写历史题目字段。"""
from datetime import UTC, datetime

from polaris.db.models import InterviewQuestion, Post, RawItem
from polaris.tools.knowledge import question_row


def row(title, *, round=None, raw_json=None):
    now = datetime(2026, 10, 23, tzinfo=UTC)
    q = InterviewQuestion(id=7, question="如何验证工具返回？", round=round,
                          material="firsthand", attribution="explicit", evidence="如何验证工具返回？")
    p = Post(id=3, published_at=now)
    raw = RawItem(id=4, source="manual", raw_text=f"{title}\n如何验证工具返回？\n\n评论：\n别家三面",
                  raw_json=raw_json or {}, url="https://example.com/interview", fetched_at=now)
    return q, question_row(q, p, raw)


def test_unknown_question_round_keeps_post_title_and_explains_scope():
    q, result = row("合成Agent实习一面面经")
    assert result["post_title"] == "合成Agent实习一面面经"
    assert result["round"] is None and q.round is None
    assert result["round_scope"] == "question"
    assert "不能推断整帖未标轮次" in result["round_note"]


def test_explicit_question_round_survives_multi_round_post_title():
    q, result = row("合成Agent一面二面面经", round="2面")
    assert result["round"] == "2面" and q.round == "2面"
    assert result["post_title"] == "合成Agent一面二面面经"


def test_native_title_is_used_and_comments_are_not_post_metadata():
    _, result = row("旧抓取标题", raw_json={"note": {"title": "合成二面记录", "desc": "如何验证工具返回？"}})
    assert result["post_title"] == "合成二面记录"
    assert "三面" not in result["post_title"]


def test_no_title_round_does_not_create_a_round_from_comment():
    q, result = row("合成面经")
    assert result["round"] is None and q.round is None
    assert result["post_title"] == "合成面经"
