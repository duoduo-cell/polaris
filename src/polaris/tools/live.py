"""source_open / platform_search：现查。纵向打开原链接看最新的，横向去平台现场搜。规矩见 polaris/live.py。

只给你自己的主 agent 用：不进 MCP（会用到你本人登录的浏览器），每次回答最多 3 次（tools/runtime.py 里数）。
"""
from typing import Literal

from pydantic import BaseModel, Field

from polaris.tools.base import ToolError, ToolSpec

LIVE_PER_ANSWER = 3


class SourceOpenArgs(BaseModel):
    ref: str = Field(..., description="要打开原链接的编号：岗位 J-12、帖子 P-3、刚现查到的 R-45")


def source_open(args: SourceOpenArgs) -> dict:
    from polaris.live import LiveUnavailable, open_source

    try:
        return open_source(args.ref.strip())
    except LiveUnavailable as e:
        raise ToolError(f"现在打不开：{e}", fix="如实告诉用户打不开的原因，给出库里存的版本和采集时间", example=None) from e


class PlatformSearchArgs(BaseModel):
    platform: Literal["xhs", "maimai", "boss", "tencent"] = Field(
        ..., description="xhs 小红书（笔记，按最新、一周内）、maimai 脉脉（职言，按时间）、boss Boss直聘（岗位）、tencent 腾讯招聘官网")
    query: str = Field(..., min_length=1, max_length=40, description="搜索词，短一点，如：字节 Agent 实习 面经")
    limit: int = Field(5, ge=1, le=8, description="最多带回几条")
    city: str | None = Field(None, description="只对 Boss 有用：城市，如 北京")


def platform_search(args: PlatformSearchArgs) -> dict:
    from polaris.live import LiveUnavailable, search

    try:
        return search(args.platform, args.query.strip(), args.limit, args.city)
    except LiveUnavailable as e:
        raise ToolError(f"现在查不了：{e}", fix="如实告诉用户原因，改用本地库（posts_search / jobs_search）并说明是哪天采的",
                        example=None) from e


TOOLS = [
    ToolSpec(name="source_open", args_schema=SourceOpenArgs, func=source_open, live=True,
             description="打开一个岗位或帖子的原链接，读现在的页面，和库里存的版本比：页面可读取、明确关闭或暂时无法核验；页面可读取不代表岗位仍在招聘。"
                         "网络错误、验证拦截、限流、空结果或加载内容不足都属于无法核验；HTTP 200 本身也不保证仍在招。"
                         "页面有变化时会显示差异；帖子可能有新评论。"
                         "用户问'还在招吗''原文现在怎么写''内推还有效吗'时用。慢（十几秒到一分钟），每次回答最多现查 3 次。",
             example='{"ref": "J-12"}'),
    ToolSpec(name="platform_search", args_schema=PlatformSearchArgs, func=platform_search, live=True,
             description="现在就去小红书、脉脉、Boss 直聘或腾讯招聘官网搜一个词，带回最新的几条（正文、时间、链接，用 R- 编号引用）。"
                         "用户要最新消息、本地库里没有或太旧时用；先查本地库够用就不用。慢，每次回答最多现查 3 次。",
             example='{"platform": "xhs", "query": "字节 Agent 实习 面经"}'),
]
