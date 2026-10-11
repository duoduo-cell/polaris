"""检索：分块、RRF 融合、查询扩展、MMR 去冗余、评测指标。纯函数，不调模型。"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from polaris.eval.retrieval import metrics
from polaris.search.chunking import job_chunks, post_chunks, split_jd, split_parts
from polaris.search.retrieve import RRF_K, WEIGHTS, Hit, expand, mmr, rrf


def job(**kw):
    base = dict(id=7, company="腾讯", unit="CSIG/腾讯地图", title="腾讯地图-Agent系统架构师", cities=["北京", "深圳"], city=None,
                job_type="social", posted_at=None, is_synthetic=False,
                jd_text="腾讯地图-Agent系统架构师\n公司：腾讯（CSIG）\n地点：北京\n岗位职责：\n1.负责 Agent 系统架构设计，"
                        "包括规划、记忆与工具调用的整体方案。\n任职要求：\n1.熟悉 LangGraph、RAG，有大模型应用落地经验；"
                        "\n2.三年以上后端开发经验。")
    return SimpleNamespace(**{**base, **kw})


def test_job_split_into_duty_and_requirement_with_context_header():
    cs = job_chunks(job())
    assert [c.kind for c in cs] == ["job_duty", "job_req"]
    assert cs[0].header == "腾讯 ｜ CSIG/腾讯地图 ｜ 腾讯地图-Agent系统架构师 ｜ 北京、深圳 ｜ 社招 ｜ 职责"
    assert "公司：" not in cs[0].body and "地点：" not in cs[0].body  # 表头行已在上下文头里
    assert cs[1].body.startswith("任职要求") and "LangGraph" in cs[1].body
    assert len({c.key for c in cs}) == 2 and cs[0].content_hash != cs[1].content_hash


def test_short_jd_stays_one_chunk():
    cs = job_chunks(job(jd_text="做 Agent"))
    assert [c.kind for c in cs] == ["job"] and cs[0].key == "job:7"
    assert split_jd("岗位职责：做事\nRequirements: Python") == ("岗位职责：做事", "Requirements: Python")


def test_post_and_questions():
    post = SimpleNamespace(id=3, big_companies=["字节跳动"], companies=[], post_type="面经", roles=["Agent开发"],
                           published_at=datetime(2026, 9, 28, tzinfo=UTC), interview_questions=["手撕 LRU", " "])
    cs = post_chunks(post, "字节 Agent 一面 #面经[话题]#\n问了 RAG", "xhs")
    assert [c.kind for c in cs] == ["post", "question"]
    assert cs[0].header == "小红书 ｜ 字节跳动 ｜ 面经 ｜ 2026-09-28" and "[话题]" not in cs[0].body
    assert cs[1].key == "post:3:q0" and cs[1].header.startswith("面试题 ｜ 字节跳动")


def test_long_post_parts_overlap_by_one_sentence():
    text = "".join(f"第{i}句话讲一个面试问题。" for i in range(200))
    parts = split_parts(text, target=300)
    assert all(len(p) <= 320 for p in parts) and len(parts) > 5
    for a, b in zip(parts, parts[1:], strict=False):
        assert a.split("。")[-2] + "。" == b.split("。")[0] + "。"  # 后一块以前一块的最后一句开头


def test_rrf_weights_and_normalisation():
    fused = rrf({"vector": [(1, 0.9), (2, 0.8)], "keyword": [(2, 9.0), (3, 5.0)]})
    assert fused[2][0] > fused[1][0] > fused[3][0]  # 两路都有的排最前
    assert fused[2][1] == {"vector": 2, "keyword": 1}
    best = (WEIGHTS["vector"] + WEIGHTS["keyword"]) / (RRF_K + 1)
    assert fused[1][0] == pytest.approx(WEIGHTS["vector"] / (RRF_K + 1) / best)
    assert all(0 < v[0] <= 1 for v in fused.values())


def test_expand_adds_company_aliases_only():
    out = expand("字节 Agent 面经")
    assert out.startswith("字节 Agent 面经") and "字节跳动" in out
    assert expand("Agent 面经") == "Agent 面经"


def hit(i: int, final: float) -> Hit:
    return Hit(chunk_id=i, key=f"k{i}", kind="question", job_id=None, post_id=i, header="", body="", final=final)


def test_mmr_demotes_near_duplicates():
    hits = [hit(1, 0.9), hit(2, 0.89), hit(3, 0.7)]
    tokens = {1: frozenset({"kv", "cache", "显存"}), 2: frozenset({"kv", "cache", "显存"}), 3: frozenset({"rag", "召回"})}
    assert [h.chunk_id for h in mmr(hits, tokens, 2)] == [1, 3]  # 同一道题的转载被压下去
    assert [h.chunk_id for h in mmr(hits, tokens, 2, lam=1.0)] == [1, 2]  # λ=1 就是纯按分数


def test_ir_metrics():
    rel = {"a": 2, "b": 1, "c": 0, "d": 1}
    m = metrics(["c", "a", "x", "b"], rel, k=4)
    assert m["mrr"] == 0.5 and m["recall"] == pytest.approx(2 / 3)
    assert m["map"] == pytest.approx((1 / 2 + 2 / 4) / 3)
    assert 0 < m["ndcg"] < 1
    assert metrics(["a", "b", "d"], rel, k=3)["ndcg"] == pytest.approx(1.0)
