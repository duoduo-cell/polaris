"""检索用的分块（`TECH.md` 第 4 节）。参考 WeKnora 的上下文头和父子分块，按我们的短文本简化。

- 岗位：职责、要求分开成块（在"职位要求""任职要求""Requirements"等标记处切开），太短就整篇一块。
  每块前加上下文头"公司 ｜ 部门 ｜ 岗位名 ｜ 城市 ｜ 类型"，块单独拿出来也知道说的是哪个岗位。
- 帖子：不到 1,500 字整篇一块（10-11 实测 427 篇只有 4 篇超过）；更长的按句子切成约 500 字的子块，相邻子块重叠一句，
  检索命中子块、回答时用整帖（父文档就是 posts 那一行，不另存）。
- 面经里的每道面试题单独成块，挂在原帖下面，头里带公司和日期：问"字节面试问了什么"能直接命中题目。

块的编号（key）由来源和位置决定，重建时不变；正文的指纹（content_hash）变了才重新算向量。
"""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

POST_WHOLE_MAX = 1500  # 帖子整篇一块的上限（字）
PART_TARGET = 500  # 长帖子块的目标长度（字）
MIN_SECTION = 40  # 职责或要求短于这个就不单独成块
JOB_TYPES = {"intern": "实习", "campus": "校招", "social": "社招"}
REQ_MARK = re.compile(r"^\s*【?(职位要求|任职要求|任职资格|岗位要求|Requirements|Who We Look For|Qualifications)", re.M | re.I)
META_LINE = re.compile(r"^(公司|地点|类型|类别|更新时间|工作地点)：")
TOPIC_TAG = re.compile(r"#[^#\s]*\[话题\]#|\[[^\]]{1,6}[RH]\]")
SENTENCE_END = re.compile(r"(?<=[。！？!?；;\n])")


@dataclass(frozen=True)
class Chunk:
    key: str  # 稳定编号，如 job:12:duty、post:5:0、post:5:q3
    kind: str  # job / job_duty / job_req / post / post_part / question
    header: str
    body: str
    job_id: int | None = None
    post_id: int | None = None
    companies: tuple[str, ...] = ()
    published_at: datetime | None = None
    is_synthetic: bool = False

    @property
    def content(self) -> str:
        return f"{self.header}\n{self.body}"

    @property
    def content_hash(self) -> str:
        return hashlib.blake2b(self.content.encode("utf-8"), digest_size=16).hexdigest()


def header(*parts: Any) -> str:
    return " ｜ ".join(str(p) for p in parts if p)


def _clean_jd(title: str | None, jd: str | None) -> str:
    """去掉采集时拼进正文的表头（岗位名、公司、地点、类型）：这些在上下文头里已经有了，留着只会干扰关键词检索。"""
    lines = [ln.rstrip() for ln in (jd or "").splitlines()]
    keep = [ln for ln in lines if ln.strip() and ln.strip() != (title or "").strip() and not META_LINE.match(ln.strip())]
    return "\n".join(keep).strip()


def split_jd(text: str) -> tuple[str, str]:
    """职责（含团队介绍）和要求。找不到"要求"标记就全算职责。"""
    m = REQ_MARK.search(text)
    if not m:
        return text, ""
    return text[: m.start()].strip(), text[m.start():].strip()


def job_chunks(job: Any) -> list[Chunk]:
    head = header(job.company, job.unit, job.title, "、".join(job.cities or []) or job.city,
                  JOB_TYPES.get(job.job_type or "", job.job_type))
    text = _clean_jd(job.title, job.jd_text)
    duty, req = split_jd(text)
    common = dict(job_id=job.id, companies=(job.company,) if job.company else (), published_at=job.posted_at,
                  is_synthetic=job.is_synthetic)
    if len(duty) < MIN_SECTION or len(req) < MIN_SECTION:
        return [Chunk(key=f"job:{job.id}", kind="job", header=head, body=text or job.title or "", **common)]
    return [Chunk(key=f"job:{job.id}:duty", kind="job_duty", header=header(head, "职责"), body=duty, **common),
            Chunk(key=f"job:{job.id}:req", kind="job_req", header=header(head, "要求"), body=req, **common)]


def clean_post(text: str | None) -> str:
    return re.sub(r"[ \t]+", " ", TOPIC_TAG.sub("", text or "")).strip()


def split_parts(text: str, target: int = PART_TARGET) -> list[str]:
    """按句子切成约 target 字的块，相邻块重叠一句（问题和答案跨块时不至于断开）。"""
    sentences = [s for s in SENTENCE_END.split(text) if s.strip()]
    parts: list[str] = []
    cur: list[str] = []
    for s in sentences:
        if cur and sum(map(len, cur)) + len(s) > target:
            parts.append("".join(cur).strip())
            cur = [cur[-1]]  # 重叠一句
        cur.append(s)
    if cur and (not parts or "".join(cur).strip() != parts[-1]):
        parts.append("".join(cur).strip())
    return parts


SOURCE_NAMES = {"xhs": "小红书", "maimai": "脉脉"}


def post_chunks(post: Any, raw_text: str, source: str) -> list[Chunk]:
    companies = tuple(post.big_companies or post.companies or ())
    date = post.published_at.date().isoformat() if post.published_at else None
    head = header(SOURCE_NAMES.get(source, source), "、".join(companies), post.post_type, date)
    common = dict(post_id=post.id, companies=companies, published_at=post.published_at)
    text = clean_post(raw_text)
    out: list[Chunk] = []
    if len(text) <= POST_WHOLE_MAX:
        out.append(Chunk(key=f"post:{post.id}", kind="post", header=head, body=text, **common))
    else:
        out += [Chunk(key=f"post:{post.id}:{i}", kind="post_part", header=header(head, f"第 {i + 1} 段"), body=p, **common)
                for i, p in enumerate(split_parts(text))]
    roles = "、".join(post.roles or [])
    for i, q in enumerate(post.interview_questions or []):
        q = (q if isinstance(q, str) else str(q)).strip()
        if q:
            out.append(Chunk(key=f"post:{post.id}:q{i}", kind="question",
                             header=header("面试题", "、".join(companies), roles, date), body=q, **common))
    return out
