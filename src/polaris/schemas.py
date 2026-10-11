"""岗位字段的数据结构。合成数据的标准答案和 M2 的抽取结果用同一个结构，评测时逐字段对比。"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class JobFacts(BaseModel):
    company: str | None = Field(None, description="公司名")
    title: str | None = Field(None, description="岗位名称，照原文")
    directions: list[str] = Field(default_factory=list,
                                  description="开发方向，如 大模型应用、RAG、Agent、模型评测、数据、多模态、推理加速")
    city: str | None = Field(None, description="工作城市；远程写 远程")
    salary_text: str | None = Field(None, description="薪资原文，如 300-400元/天")
    salary_min: float | None = None
    salary_max: float | None = None
    salary_unit: Literal["day", "month", "year"] | None = None
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="实习、校招、社招")
    can_convert: bool | None = Field(
        None, description="写了有转正机会就是 true，写了不提供转正就是 false，没提到填 null")
    posted_at: date | None = Field(None, description="发布或更新日期，YYYY-MM-DD")
    deadline: date | None = Field(None, description="投递截止日，YYYY-MM-DD")
    start_text: str | None = Field(None, description="最早到岗时间原文，如 尽快到岗、2026年11月")
    duration_months: int | None = Field(None, description="实习时长，单位月")
    days_per_week: int | None = Field(None, description="每周到岗天数")
    requirements: list[str] = Field(default_factory=list, description="任职要求要点，每条一句")


class PostFacts(BaseModel):
    """小红书笔记（面经、内推、招聘、经验帖）里抽出来的信息。"""

    post_type: Literal["内推", "招聘", "面经", "经验", "求助", "其他"] = Field(
        ..., description="内推：员工帮部门找人；招聘：岗位信息；面经：面试过程和题目；经验：求职或实习经验；求助：发帖人在问问题")
    companies: list[str] = Field(default_factory=list,
                                 description="提到的公司，写规范全称。谐音和 emoji 要还原，如 🫘音→抖音、tou条→今日头条")
    team: str | None = Field(None, description="部门或团队，如 抖音电商、火山引擎、TEG")
    roles: list[str] = Field(default_factory=list, description="涉及的岗位名")
    directions: list[str] = Field(default_factory=list, description="技术方向，如 Agent、RAG、大模型、多模态、推荐")
    city: str | None = Field(None, description="工作城市（base）")
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="实习、校招、社招")
    grad_year: str | None = Field(None, description="对毕业年份的要求，如 2027届、2028年及以后毕业")
    days_per_week: int | None = None
    duration_months: int | None = None
    can_convert: bool | None = Field(None, description="写了有转正机会就是 true")
    has_referral: bool = Field(False, description="发帖人或评论里明确可以内推")
    interview_rounds: int | None = Field(None, description="面经里提到几轮面试")
    interview_questions: list[str] = Field(default_factory=list, description="面经里的题目，每条一句，最多 15 条")
    advice: list[str] = Field(default_factory=list, description="作者给的建议，每条一句，最多 5 条")
    summary: str = Field(..., description="一句话概括这条笔记对求职者有什么用")


# 评测时逐个对比的字段（requirements 和 directions 是开放文本，单独评）
SCORED_FIELDS = ("company", "title", "city", "salary_min", "salary_max", "salary_unit", "job_type",
                 "can_convert", "posted_at", "deadline", "duration_months", "days_per_week")
