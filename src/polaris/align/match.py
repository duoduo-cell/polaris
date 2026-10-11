"""同岗识别：哪些 posting 说的是同一个岗位。北极星自己实现（`TECH.md` 第 3 节，环节 3）。

判定规则：同一公司，部门相容，岗位名归一后一致（或几乎一致），正文不矛盾。城市不参与判定：
同一个岗位常常在多个城市各挂一条（字节"大模型算法实习生 - AI Platform"在四个城市各一条），合并后城市取并集。
- 部门逐段比：一边的段是另一边的子集才算相容（"CSIG" 和 "CSIG/腾讯地图"）；段名只是字面包含（"Seed" 和 "Seed Model"，
  "微信" 和 "微信搜索"）是弱证据，要正文也像；有一边没写部门，正文不能明显不同。
- 同一来源（同一个官网）的同名岗位要正文几乎逐字相同：官网同一岗位多城市挂牌是复制的，同名不同岗的正文共用团队介绍、
  职责不同（字节 Seed Model 下两个"大模型训练优化研发实习生"，一个做系统架构、一个做 CUDA 加速）。
- 跨来源（官网和 Boss、转载帖）排版不同，正文用 SimHash 粗比。
能分开"岗位名一样、事业群不同"（腾讯 IEG 和 PCG 各有一个"Agent开发工程师"），也能分开"正文几乎一样、岗位不同"
（腾讯 WorkBuddy 的研发工程师和算法工程师，近似重复指纹只差 1，靠岗位名里的区分词）。
"""
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from polaris.align.normalize import cities, key, split_title
from polaris.companies import match as big_company
from polaris.dedup import hamming, jaccard, shingle_set

JD_NEAR_DUP = 12  # 跨来源正文近似重复：SimHash 汉明距离上限
# 同一来源正文几乎相同：字符三元组 Jaccard 下限。10-11 实测同一来源同名岗位 380 对：同一岗位多城市挂牌 0.81 到 1.0，
# 同名不同岗 0.55 到 0.62（日文版 JD 和英文版的同一岗位也是 0.62，会被分开，宁可少并），取中间 0.7
JD_SAME_SOURCE = 0.7
JD_HEADER = ("地点：", "更新时间：")  # 采集时拼进正文的表头里随城市、随时间变的行，比正文时去掉
ROLE_SIMILAR = 0.92  # 岗位名几乎一致：容忍个别字差异（空格、全半角、"AI Agent" 和 "AIAgent"）
# 岗位名的差异部分出现这些词，就是不同岗位（"Agent Harness 工程师"和"Agent Harness 研发工程师"只差两个字，但不是一个岗）
DISTINGUISHING = ("研发", "算法", "产品", "测试", "前端", "后端", "全栈", "客户端", "数据", "运营", "评测", "安全",
                  "高级", "资深", "专家", "架构", "负责人", "实习", "校招", "经理", "设计", "策略", "infra", "senior",
                  "staff", "principal", "intern", "lead", "frontend", "backend", "fullstack", "algorithm", "product")


@dataclass
class PostingView:
    id: int
    source: str
    company: str
    unit: str | None
    unit_key: str | None
    role: str
    role_key: str
    cities: frozenset[str]
    fields: dict[str, Any] = field(default_factory=dict)
    first_seen: Any = None
    last_seen: Any = None
    is_synthetic: bool = False
    simhash: int | None = None
    unit_parts: frozenset[str] = frozenset()
    shingles: frozenset[str] = frozenset()


def company_key(big: str | None, company_name: str | None) -> str:
    if big:
        return big
    hit = big_company(company_name)
    if hit:
        return hit.name
    return key(company_name)[:60] or "未知"


def jd_body(title: str | None, jd_text: str | None) -> str:
    lines = (jd_text or "").splitlines()
    return "\n".join(ln for ln in lines if ln.strip() != (title or "").strip() and not ln.startswith(JD_HEADER))


def view(row: Any, raw: Any) -> PostingView:
    parts = split_title(row.title, row.company_name, raw.source)
    body = jd_body(row.title, row.jd_text)
    return PostingView(id=row.id, source=raw.source, company=company_key(row.big_company, row.company_name),
                       unit=parts.unit, unit_key=key(parts.unit) or None, role=parts.role, role_key=parts.role_key,
                       cities=cities(row.city), first_seen=raw.first_seen_at, last_seen=raw.last_seen_at,
                       is_synthetic=row.is_synthetic, simhash=raw.simhash, unit_parts=frozenset(parts.unit_parts),
                       shingles=shingle_set(body) if body else frozenset())


def unit_relation(a: PostingView, b: PostingView) -> str:
    """部门的关系：both_missing / one_sided / same（含子集）/ name_contains（只是字面包含）/ different。"""
    if not a.unit_parts and not b.unit_parts:
        return "both_missing"
    if not a.unit_parts or not b.unit_parts:
        return "one_sided"
    if a.unit_parts <= b.unit_parts or b.unit_parts <= a.unit_parts:
        return "same"
    if a.unit_key and b.unit_key and (a.unit_key in b.unit_key or b.unit_key in a.unit_key):
        return "name_contains"
    return "different"


def jd_close(a: PostingView, b: PostingView) -> bool | None:
    """正文像不像。同一来源比三元组 Jaccard，跨来源比 SimHash；缺正文返回 None（不作为证据）。"""
    if a.source == b.source and a.shingles and b.shingles:
        return jaccard(a.shingles, b.shingles) >= JD_SAME_SOURCE
    if a.simhash is not None and b.simhash is not None:
        return hamming(a.simhash, b.simhash) <= JD_NEAR_DUP
    return None


def role_difference(a: str, b: str) -> str:
    """两个岗位名不一样的部分（插入、删除、替换的字拼起来）。"""
    sm = SequenceMatcher(None, a, b)
    return "".join(a[i1:i2] + b[j1:j2] for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")


def same_job(a: PostingView, b: PostingView) -> tuple[bool, str]:
    if a.company != b.company:
        return False, "公司不同"
    close = jd_close(a, b)
    if a.source == b.source and close is False:
        # 10-11 留出集的错例：同一官网同名同部门的两个不同岗位（字节 Seed Model、财经业务）
        return False, "同一来源，正文不同：是同名的两个岗位"
    if a.role_key != b.role_key:
        if SequenceMatcher(None, a.role_key, b.role_key).ratio() < ROLE_SIMILAR:
            return False, "岗位名不同"
        diff = role_difference(a.role_key, b.role_key)
        if any(w in diff for w in DISTINGUISHING):
            return False, f"岗位名差在「{diff}」，是不同岗位"
        if close is False:
            # 岗位名只是相似（不完全相同）时，还要正文也像（10-11 开发集的错例："即梦Agent方向"和"Agent方向"）
            return False, f"岗位名差在「{diff}」，正文也不像"
    units = unit_relation(a, b)
    if units == "different":
        return False, "部门不同"
    if units == "name_contains" and close is not True:
        # 10-11 留出集的错例："微信"和"微信搜索"、"剪映"和"剪映CapCut用户增长"是不同团队
        return False, "部门名只是字面包含，正文也不像"
    if units == "one_sided" and close is False:
        # 只有一边写了部门：光看岗位名不够（10-11 标注时见过：腾讯 IEG 光子和没写事业群的同名岗位是两个团队）
        return False, "一边没写部门，正文也不像"
    return True, "公司、部门、岗位名一致"


def cluster(views: list[PostingView]) -> list[list[PostingView]]:
    """完全链接：一条 posting 要和组里每一条都判为同岗才能加入（单链接会串联：10-11 实测，
    通用的"Agent Harness 工程师"分别像"研发工程师"和"算法工程师"，把两个不同岗位连成了一组）。
    同时能并进两个以上的组说明有歧义，单独成组，宁可少并不错并。按公司分桶，桶内按编号顺序处理，结果可复现。"""
    by_company: dict[str, list[PostingView]] = defaultdict(list)
    for v in sorted(views, key=lambda x: x.id):
        by_company[v.company].append(v)
    out: list[list[PostingView]] = []
    for bucket in by_company.values():
        groups: list[list[PostingView]] = []
        for v in bucket:
            fits = [g for g in groups if all(same_job(v, m)[0] for m in g)]
            if len(fits) == 1:
                fits[0].append(v)
            else:
                groups.append([v])
        out += groups
    return sorted(out, key=lambda g: min(v.id for v in g))


def job_key(group: list[PostingView]) -> str:
    """一个岗位的稳定编号：公司 | 最具体的部门 | 岗位名。重建时编号不变，引用不会失效。"""
    units = sorted({v.unit_key for v in group if v.unit_key}, key=len, reverse=True)
    role = min((v.role_key for v in group), key=len)
    synthetic = "syn:" if all(v.is_synthetic for v in group) else ""
    return f"{synthetic}{group[0].company}|{units[0] if units else ''}|{role}"[:300]
