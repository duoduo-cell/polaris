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
