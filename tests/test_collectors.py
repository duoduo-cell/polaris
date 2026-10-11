"""采集相关：大厂名单匹配、小红书只读白名单。不联网。"""
import pytest

from polaris.collectors import xhs
from polaris.collectors.tencent import to_text
from polaris.companies import is_big, match


def test_big_company_aliases():
    assert match("字节跳动").name == "字节跳动"
    assert match("TikTok 搜索团队").name == "字节跳动"
    assert match("阿里云智能").name == "阿里巴巴"
    assert match("Kimi 团队").name == "月之暗面"
    assert not is_big("某某小科技有限公司")
    assert not is_big("credit card")  # 英文别名按单词边界匹配，RED 不会命中 credit


def test_xhs_write_tools_are_blocked_before_any_network_call():
    for name in ("like_feed", "post_comment_to_feed", "publish_content", "favorite_feed", "get_my_profile"):
        with pytest.raises(xhs.BlockedTool):
            xhs.call(name, {})


def test_tencent_text_includes_requirement():
    text = to_text({"RecruitPostName": "Agent Harness 工程师", "BGName": "CSIG", "LocationName": "上海",
                    "Responsibility": "职责A", "Requirement": "要求B"})
    assert "腾讯（CSIG）" in text and "职责A" in text and "要求B" in text


def test_boss_write_commands_are_blocked():
    from polaris.collectors import boss

    for cmd in ("greet", "batch-greet", "chat", "applied", "me"):
        with pytest.raises(boss.BlockedCommand):
            boss.run_boss([cmd, "x"])


def test_boss_keeps_only_big_companies_and_drops_recruiter_names():
    from polaris.collectors.base import Query
    from polaris.collectors.boss import BossCallFailed, BossCollector

    calls: list[list[str]] = []
    cards = [{"encryptJobId": "J1", "securityId": "S1", "jobName": "Agent 实习生", "brandName": "字节跳动",
              "bossName": "张三", "bossTitle": "HR"},
             {"encryptJobId": "J2", "securityId": "S2", "jobName": "AI 实习", "brandName": "某某小科技"},
             {"encryptJobId": "J3", "securityId": "S3", "jobName": "大模型实习", "brandName": "腾讯"}]

    def runner(args):
        calls.append(args)
        if args[0] == "search":
            return {"jobList": cards, "hasMore": False}
        if args[1] == "S3":
            raise BossCallFailed("rate_limited", "code=9")
        return {"jobInfo": {"jobName": "Agent 实习生", "postDescription": "做 Agent", "bossName": "张三"},
                "bossInfo": {"name": "张三"}}

    c = BossCollector(cities=("北京",), scales=("10000人以上",), pause=(0, 0), known=set(), runner=runner)
    items = list(c.collect(Query(keywords=["Agent 实习", "RAG 实习"])))
    assert [i.external_id for i in items] == ["J1"]
    assert "张三" not in items[0].raw_text and "张三" not in str(items[0].raw_json)
    assert c.stats.skipped_small == 1 and c.stats.rate_limited == 1 and c.stats.stopped == "rate_limited"
    assert all(a[0] != "search" or a[1] == "Agent 实习" for a in calls)  # 限流后第二个词不再搜
    assert ["--job-type", "实习"] == calls[0][-2:]


def test_xhs_stops_on_block_signal_and_dedupes_across_sorts():
    from polaris.collectors.base import Query
    from polaris.collectors.xhs import XhsCollector

    feeds = [{"id": "n1", "xsecToken": "t", "modelType": "note", "noteCard": {"displayTitle": "字节 Agent 实习内推"}}]
    calls: list[str] = []

    def caller(name, args):
        calls.append(name)
        if name == "search_feeds":
            return {"feeds": feeds}
        return {"data": {"note": {"noteId": "n1", "title": "t", "desc": "面试官频繁追问验证细节"}, "comments": {}}}

    c = XhsCollector(pause=(0, 0), known=set(), caller=caller, sorts=("最新", "最多点赞"), open_profiles=False)
    items = list(c.collect(Query(keywords=["字节 Agent"])))
    assert [i.external_id for i in items] == ["n1"]  # 两种排序都搜到同一条，只打开一次
    assert calls == ["search_feeds", "get_feed_detail", "search_feeds"] and c.stats.stopped is None  # 正文里的"频繁""验证"不算被拦

    def blocked(name, args):
        raise RuntimeError("访问频繁，请稍后再试")

    c2 = XhsCollector(pause=(0, 0), known=set(), caller=blocked)
    assert list(c2.collect(Query(keywords=["a", "b", "c"]))) == []
    assert c2.stats.stopped and "疑似被拦" in c2.stats.stopped and c2.stats.calls == 1  # 第一次就停，不再试


def test_xhs_stops_after_consecutive_failures():
    from polaris.collectors.base import Query
    from polaris.collectors.xhs import XhsCollector

    def flaky(name, args):
        raise RuntimeError("context deadline exceeded")

    c = XhsCollector(pause=(0, 0), known=set(), caller=flaky)
    list(c.collect(Query(keywords=["a", "b", "c", "d", "e"])))
    assert c.stats.calls == 3 and "连续失败" in c.stats.stopped
