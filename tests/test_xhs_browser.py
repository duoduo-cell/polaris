"""小红书真实浏览器版：被拦和掉登录的识别、地址格式、写操作一律拒绝。"""
import pytest

from polaris.collectors.xhs_browser import BrowserCaller, note_url, page_problem, search_url


def test_page_problem():
    assert page_problem("https://www.xiaohongshu.com/search_result?keyword=a", "Agent 实习 笔记列表") is None
    assert "captcha" in page_problem("https://www.xiaohongshu.com/website-login/captcha?redirect=x", "")
    assert "安全验证" in page_problem("https://www.xiaohongshu.com/explore/1", "请完成安全验证后继续")
    assert "登录后查看" in page_problem("https://www.xiaohongshu.com/explore/1", "登录后查看更多内容")


def test_urls():
    assert search_url("字节 Agent").endswith("keyword=%E5%AD%97%E8%8A%82+Agent&source=web_explore_feed")
    assert note_url("abc", "tok") == "https://www.xiaohongshu.com/explore/abc?xsec_token=tok&xsec_source=pc_feed"


def test_write_tools_refused_without_touching_browser():
    caller = BrowserCaller(context=None)  # 没有浏览器也能判断：写操作根本不会走到浏览器
    for name in ("post_comment_to_feed", "like_feed", "publish_content"):
        with pytest.raises(PermissionError):
            caller(name, {})


def test_rate_limit_page_is_a_block():
    url = "https://www.xiaohongshu.com/website-login/error?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2Fexplore%2F1"
    problem = page_problem(url, "安全限制\n访问频繁，请稍后再试\n300013\n我要反馈")
    assert "website-login" in problem and "访问频繁" in problem  # 10-11 实际碰到的页面
