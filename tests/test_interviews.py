"""图片面经与原句定位的业务回归，合成资料，不调用模型。"""
from types import SimpleNamespace

import pytest

from polaris.interviews import _end_of, profile


def post(questions, details):
    return SimpleNamespace(post_type="面经", big_companies=["字节跳动"],
                           interview_questions=questions, evidence_profile={"questions": details},
                           roles=[], id=1, published_at=None)


def test_normalized_evidence_position_requires_the_whole_quote():
    body = "一面\n解释一下缓存原理\n二面\n解释一下缓存原理\n以及\u200b失效策略"
    evidence = "解释一下缓存原理，以及失效策略"
    assert _end_of(body, evidence) == len(body)
    assert _end_of(body, "解释一下缓存原理，以及不存在的策略") == -1
    assert _end_of(body, "\u200b") == -1


def test_invisible_characters_and_round_prefixes_keep_image_question_rounds():
    raw = ("字节面经\n正文见图\n\n评论：\n腾讯三面\n\n图片里的文字：\n[图1]\n"
           "一面：\n解释一下\u200b注意力机制\n[图2]\n二面：\n如何设计\u00a0检索流水线")
    p = post(["一面：解释一下注意力机制", "二面：如何设计检索流水线"],
             [{"question": "解释一下注意力机制", "evidence": "解释一下注意力机制"},
              {"question": "如何设计检索流水线", "evidence": "如何设计检索流水线"}])
    got = profile(p, raw, {"note": {"title": "字节面经", "desc": "正文见图"}})
    assert [q["round"] for q in got["questions"]] == ["1面", "2面"]
    assert [q["company"] for q in got["questions"]] == ["字节跳动", "字节跳动"]
    assert got["comment_companies"] == ["腾讯"]
    assert all(q["evidence"] for q in got["questions"])


def test_round_prefix_matches_specific_detail_before_a_shorter_question():
    raw = "字节面经\n一面\n解释一下注意力机制\n二面\n解释一下注意力机制的复杂度"
    p = post(["二面：解释一下注意力机制的复杂度"],
             [{"question": "解释一下注意力机制", "evidence": "解释一下注意力机制"},
              {"question": "解释一下注意力机制的复杂度", "evidence": "解释一下注意力机制的复杂度"}])
    got = profile(p, raw)["questions"][0]
    assert got["round"] == "2面"
    assert got["evidence"] == "解释一下注意力机制的复杂度"


def test_missing_or_ambiguous_evidence_does_not_invent_a_round():
    raw = "字节面经\n一面\n解释一下注意力机制\n二面\n解释一下检索流水线"
    p = post(["三面：解释一下注意力机制和检索流水线的整体设计"],
             [{"question": "解释一下注意力机制", "evidence": "解释一下注意力机制"},
              {"question": "检索流水线", "evidence": "解释一下检索流水线"}])
    got = profile(p, raw)["questions"][0]
    assert got["evidence"] is None and got["round"] is None


def test_timeline_and_question_wording_do_not_become_round_headings():
    raw = ("字节面经\n8.03 一面\n8.10 二面\n\n图片里的文字：\n[图1]\n"
           "解释一下注意力机制\n第一轮对话做 DPO，如何构建数据集？")
    p = post(["解释一下注意力机制", "第一轮对话做 DPO，如何构建数据集？"], [])
    got = profile(p, raw)["questions"]
    assert all(q["evidence"] and q["round"] is None for q in got)


def test_round_mentioned_in_a_question_does_not_override_the_section():
    raw = ("字节面经\n📋 二面｜工程专题\n解释一下注意力机制\n"
           "· 第一轮对话做 DPO，如何构建数据集？\n如何设计检索流水线")
    p = post(["如何设计检索流水线"], [])
    assert profile(p, raw)["questions"][0]["round"] == "2面"


@pytest.mark.parametrize("disclaimer,material", [
    ("并非全部为本人亲历面试原题", "study"),
    ("面试过的粉丝投稿", "uncertain"),
])
def test_compilation_and_submitted_interviews_are_not_firsthand(disclaimer, material):
    p = post(["如何设计检索流水线"], [])
    got = profile(p, f"字节面经\n一面\n如何设计检索流水线\n{disclaimer}")
    assert got["material"] == material
    assert got["questions"][0]["company"] is None
