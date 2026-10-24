"""HR 视角看你的情况、和同类求职者横向对照（polaris/career_review.py）。都只读、不调模型。"""
from typing import Literal

from pydantic import BaseModel, Field

from polaris.db.session import session_scope
from polaris.tools.base import ToolError, ToolSpec


class ProfileReviewArgs(BaseModel):
    job_id: str | None = Field(None, description="只看一个岗位，如 J-12；不填就看下面条件圈出的范围")
    city: str | None = Field(None, description="城市，如 北京")
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="intern 实习、campus 校招、social 社招")
    direction: str | None = Field(None, description="方向，如 Agent、RAG")
    max_days_per_week: int | None = Field(None, ge=1, le=7, description="用户每周最多能去几天")
    big_only: bool = Field(True, description="只看大厂名单里的公司")


def profile_review(args: ProfileReviewArgs) -> dict:
    from polaris.career_review import review
    from polaris.tools.jobs import _parse

    job_id = _parse(args.job_id, "J", "jobs_search", '{"job_id": "J-12"}') if args.job_id else None
    criteria = {"city": args.city, "job_type": args.job_type, "direction": args.direction,
                "max_days_per_week": args.max_days_per_week, "big_only": args.big_only}
    try:
        with session_scope() as s:
            return review(s, criteria, job_id)
    except LookupError as e:
        raise ToolError(str(e), "先用 jobs_search 找到岗位编号", '{"job_id": "J-12"}') from e


class PeersCompareArgs(BaseModel):
    direction: str = Field(..., description="方向，如 Agent、RAG、后训练；必填，不同方向不能混成一个门槛")
    company: str | None = Field(None, description="目标公司，如 字节；填了才有“同公司”那一层")
    job_type: Literal["实习", "日常实习", "暑期实习", "校招", "社招"] | None = Field("实习", description="招聘类型")
    since_days: int = Field(180, ge=30, le=730, description="只看最近多少天的帖子")


def peers_compare(args: PeersCompareArgs) -> dict:
    from polaris.career_review import compare

    with session_scope() as s:
        return compare(s, company=args.company, direction=args.direction, job_type=args.job_type, since_days=args.since_days)


TOOLS = [
    ToolSpec(name="profile_review",
             description="按 HR 的眼光看用户的情况：在一个范围（或一个岗位）里，资格上被哪些硬门槛挡住、各多少个岗位；"
                         "岗位常要求的技能和用户卡片对得上多少；项目、实习、论文的证据（项目写了 GitHub 链接的会读公开仓库）；"
                         "还缺哪些要问用户的信息。用户问“我在 HR 眼里怎么样”“我的简历够不够”“我还差什么”时用。",
             args_schema=ProfileReviewArgs, func=profile_review,
             example='{"city": "北京", "job_type": "intern", "direction": "Agent", "max_days_per_week": 4}'),
    ToolSpec(name="peers_compare",
             description="把用户和同类求职者的公开自述逐项对照：按可比程度分三层（同公司同方向同类型 / 同方向同类型 / 同方向），"
                         "每层给人数、结果、学历、学校粗分、论文、实习段数的已知和未写数量，单独列出拿到 offer 的人，再放上用户自己的情况。"
                         "用户问“和别人比我差在哪”“同组竞争的人什么水平”时用；样本少于 5 人要直说。",
             args_schema=PeersCompareArgs, func=peers_compare, example='{"direction": "Agent", "company": "字节", "job_type": "实习"}'),
]
