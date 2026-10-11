"""记忆里能记哪些事：属性表。每个属性说清是"一个槽只放一个值"（每周能去几天）还是"可以有多个"（会的技术），
以及按什么分槽（面试进度按公司分：字节一个、腾讯一个）。更新规则（memory/update.py）完全由这张表决定，不靠模型临场判断。
"""
import re
from dataclasses import dataclass

from polaris.companies import match as big_company


@dataclass(frozen=True)
class Attr:
    name: str
    label: str  # 卡片上显示的名字
    multi: bool  # True：一个槽可以同时有多个值（加新的不顶掉旧的）
    qualified: bool = False  # True：按 qualifier 分槽（如面试进度按公司）
    evolves: bool = False  # True：值本来就会往前走（面试一面 → 二面），新值直接更新，不当成矛盾去追问
    example: str = ""


ATTRIBUTES: dict[str, Attr] = {a.name: a for a in [
    Attr("target_role", "想找的方向", True, example="Agent 开发、大模型应用"),
    Attr("target_city", "想去的城市", True, example="北京"),
    Attr("job_type", "找什么类型", False, example="日常实习 / 暑期实习 / 校招"),
    Attr("available_from", "最早什么时候能开始", False, example="2026-11-01"),
    Attr("days_per_week", "每周能到岗几天", False, example="4"),
    Attr("duration_months", "能实习多久（月）", False, example="6"),
    Attr("grad_year", "毕业年份", False, example="2027"),
    Attr("school", "学校", False),
    Attr("degree", "学历", False, example="硕士在读"),
    Attr("major", "专业", False),
    Attr("skill", "会的技术", True, example="LangGraph、RAG"),
    Attr("project", "做过的项目", True),
    Attr("company_like", "想去的公司", True, example="字节跳动"),
    Attr("company_avoid", "不想去的公司或团队", True),
    Attr("applied", "投递过的岗位", True, qualified=True, example="qualifier=公司，value=岗位名或 J- 编号"),
    Attr("interview_stage", "面试进度", False, qualified=True, evolves=True, example="qualifier=公司，value=二面已过"),
    Attr("salary_expect", "期望薪资", False, example="日薪 300 以上"),
    Attr("constraint", "其他硬条件", True, example="不接受外包、需要能远程"),
]}


def value_key(attribute: str, value: str) -> str:
    """归一后的值：判断两个说法是不是同一个值。公司按大厂名单归一，城市去掉"市"，数字只留数字。"""
    v = re.sub(r"\s+", "", value).lower()
    if attribute in ("company_like", "company_avoid"):
        big = big_company(value)
        return big.name if big else v
    if attribute == "target_city":
        return v.removesuffix("市")
    if attribute in ("days_per_week", "duration_months", "grad_year"):
        m = re.search(r"\d+", v)
        return m.group() if m else v
    return v[:200]


def qualifier_key(qualifier: str | None) -> str | None:
    if not qualifier:
        return None
    big = big_company(qualifier)
    return big.name if big else qualifier.strip()
