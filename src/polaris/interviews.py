"""正文/评论分离，面试题证据和保守的存量修复。无需额外模型调用。"""
import re
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polaris.companies import _pattern, load, match
from polaris.db.models import InterviewQuestion, Post, RawItem
from polaris.search.locking import acquire_knowledge_write

MATERIAL_LABELS = {"firsthand": "面经记录（作者自述）", "study": "备考/经验整理", "uncertain": "资料性质待核实"}
ROUND_RE = re.compile(r"(?:第?([一二三四五六七八九十1-9])\s*面|([一二三四五六七八九十1-9])\s*轮(?:面试)?)")
NUMBERS = dict(zip("一二三四五六七八九十", range(1, 11), strict=True))
META_RE = re.compile(r"^(发布[：:]|点赞\s|作者[：:]|属地[：:]|评论数[：:])")
STUDY_RE = re.compile(r"考点清单|题库|八股整理|面试宝典|备考|面试指南|题目汇总|高频题汇总|面试攻略|培训|付费资料|课程推广|面经指南")
COMPILATION_RE = re.compile(r"(?:并非|不是|不全是|非)(?:全部|所有)?(?:为)?(?:本人|自己)(?:亲历|亲身)|(?:粉丝|读者|网友)投稿")


def normalize(text: str) -> str:
    return re.sub(r"\W+", "", text).lower()


def split_text(raw_text: str, raw_json: dict[str, Any] | None = None) -> tuple[str, str, str]:
    data = raw_json or {}
    note = data.get("note") or {}
    # 图片里的字是作者自己的截图（面经、时间线、offer），算正文；它排在评论后面（10-12 读图后才有）
    text, _, image = (raw_text or "").partition("\n\n图片里的文字：\n")
    parts = re.split(r"\n\s*评论[：:]\s*\n", text, maxsplit=1)
    body = parts[0]
    comments = parts[1] if len(parts) > 1 else ""
    if note.get("desc") is not None:
        body = f"{note.get('title') or ''}\n{note.get('desc') or ''}".strip()
    if data.get("comments"):
        comments = "\n".join(c.get("content") or "" for c in data["comments"] if isinstance(c, dict))
    if image:
        body = f"{body}\n{image}"
    lines = [x.strip() for x in body.splitlines() if x.strip() and not META_RE.match(x.strip())]
    headline = str(note.get("title") or data.get("title") or (lines[0] if lines else "求职资料"))[:120]
    return headline, "\n".join(lines), comments


def companies_in(text: str) -> list[str]:
    text = (text or "").replace("鹅厂", "腾讯").replace("🫘音", "抖音").replace("tou条", "今日头条")
    return [c.name for c in load() if any(_pattern(n).search(text) for n in c.names)]


def round_name(m: re.Match) -> str:
    n = m.group(1) or m.group(2)
    return f"{NUMBERS.get(n, n)}面"


def category(question: str) -> str:
    for name, pattern in (("手撕代码", r"手撕|力扣|leetcode|算法题|写代码|实现.*(?:排序|链表|队列)"),
                          ("项目深挖", r"项目|实习经历"), ("场景设计", r"设计|场景|系统架构"),
                          ("行为", r"自我介绍|职业规划|为什么(?:选择|想去|离职)|冲突.*同事"), ("RAG", r"rag|召回|检索增强"),
                          ("Agent", r"agent|智能体|工具调用|mcp"), ("大模型", r"大模型|llm|transformer|attention|lora|kv.cache"),
                          ("后端基础", r"数据库|sql|线程|进程|网络|http|操作系统|redis")):
        if re.search(pattern, question, re.I):
            return name
    return "其他"


def evidence_line(question: str, body: str, supplied: str | None = None) -> str | None:
    if supplied and normalize(supplied) and normalize(supplied) in normalize(body):
        return supplied[:1200]
    q = normalize(question)
    # 短标签不是问题出现过的证据。使用原文中的完整行，不用模型生成原文。
    if len(q) >= 5:
        for line in body.splitlines():
            if q in normalize(line):
                return line.strip()[:1200]
    return None


def _end_of(body: str, evidence: str) -> int:
    """证据在正文里结束的位置。模型照抄时会丢掉不可见字符（\\u200b、\\xa0），按原样找不到，
    就按归一化后的完整证据和原文位置映射找（10-12 R-920：8 道题因此挂不上"一面"）。找不到返回 -1。"""
    target = normalize(evidence)
    if not target:
        return -1
    i = body.find(evidence)
    if i >= 0:
        return i + len(evidence)
    # 保留归一化字符到原文位置的映射，完整匹配证据；不能只匹配其中一行，
    # 否则相似题目或跨行证据会被定位到更早的轮次。
    chars, ends = [], []
    for m in re.finditer(r"\w", body):
        lowered = m.group().lower()
        chars.extend(lowered)
        ends.extend([m.end()] * len(lowered))
    i = "".join(chars).find(target)
    return ends[i + len(target) - 1] if i >= 0 else -1


def round_sections(body: str) -> list[re.Match]:
    """只用题目分组标题定位轮次。投递时间线、题目中的“第一轮对话”不算分组。"""
    headings = []
    for m in ROUND_RE.finditer(body):
        start = body.rfind("\n", 0, m.start()) + 1
        end = body.find("\n", m.end())
        line_end = len(body) if end < 0 else end
        prefix, suffix = body[start:m.start()], body[m.end():line_end]
        if normalize(prefix) and not (start == 0 and companies_in(prefix)):
            continue
        if suffix and not re.match(r"[\s：:｜|（(\-—]", suffix):
            continue
        headings.append(m)
    return headings


def profile(post: Any, raw_text: str, raw_json: dict[str, Any] | None = None) -> dict[str, Any]:
    headline, body, comments = split_text(raw_text, raw_json)
    head_companies, body_companies = companies_in(headline), companies_in(body)
    company = head_companies[0] if len(head_companies) == 1 else (body_companies[0] if len(body_companies) == 1 else None)
    # 泛称/多公司帖子不把已抽出的公司集合当作题目的提问公司。
    if company is None and not body_companies and len(getattr(post, "big_companies", []) or []) == 1:
        candidate = (post.big_companies or [])[0]
        if any(_pattern(n).search(body) for c in load() if c.name == candidate for n in c.names):
            company = candidate
    material = "study" if post.post_type == "经验" or STUDY_RE.search(headline) else "uncertain"
    if post.post_type == "面经" and material != "study" and re.search(r"面经|[一二三四1-9]面|面试经历|面试官", body):
        material = "firsthand"
    if company is None and re.search(r"八股文|大厂面试特点|知识点汇总|考点整理", body) and not re.search(
            r"凭回忆|时长[：:]|面试官问|面试官让我|我(?:的|参加|经历|投|面)|一面过|二面过", body):
        material = "study"
    supplied = getattr(post, "evidence_profile", None) or {}
    if COMPILATION_RE.search(body):
        material = "uncertain" if re.search(r"(?:粉丝|读者|网友)投稿", body) else "study"
    explicit_company = supplied.get("interview_company")
    supplied_company = match(explicit_company)
    if supplied_company and supplied.get("company_evidence") and normalize(supplied["company_evidence"]) in normalize(body):
        company = supplied_company.name
    # 有多个明确公司时，旧数据只凭题目列表无法确定每道题归属。
    if len(head_companies) > 1:
        company = None
    if material != "firsthand":
        company = None
    details = supplied.get("questions") or []
    detail_by_question = {normalize(d.get("question", "")): d for d in details if isinstance(d, dict)}
    rounds = list(dict.fromkeys(round_name(m) for m in ROUND_RE.finditer(body)))
    sections = round_sections(body)
    rows = []
    for i, question in enumerate(post.interview_questions or []):
        if not isinstance(question, str) or not question.strip():
            continue
        nq = normalize(question)
        # 10-12：模型有时在题目前加"一面："，明细里的同一道题没加，按原样对不上，出处和轮次全丢（R-2876 15 道 → 1 道）
        detail = detail_by_question.get(nq)
        if detail is None:
            # 先去掉题目开头的轮次，再精确对应；有多个可能的明细时保持未知。
            bare = re.sub(rf"^\s*{ROUND_RE.pattern}\s*[：:]\s*", "", question)
            detail = detail_by_question.get(normalize(bare))
        if detail is None:
            candidates = [d for k, d in detail_by_question.items() if len(k) >= 5 and k in nq]
            detail = candidates[0] if len(candidates) == 1 else {}
        evidence = evidence_line(question, body, detail.get("evidence"))
        qcompany = company
        if detail.get("company") and evidence and material == "firsthand":
            specified = match(detail["company"])
            if specified and specified.name in companies_in(evidence):
                qcompany = specified.name
        qround = None
        if evidence:
            end = _end_of(body, evidence)
            preceding = [m for m in sections if m.start() < end] if end >= 0 else []
            if preceding:
                qround = round_name(preceding[-1])
        if qround is None and len(rounds) == 1 and sections:
            qround = rounds[0]
        interview_date = None
        if detail.get("interview_date") and evidence and detail["interview_date"] in body:
            try:
                interview_date = date.fromisoformat(detail["interview_date"])
            except ValueError:
                pass
        roles = post.roles or []
        rows.append({"key": f"post:{post.id}:q{i}", "post_id": post.id, "question": question.strip(), "company": qcompany,
                     "role": detail.get("role") if evidence and detail.get("role") and detail["role"] in body
                     else (roles[0] if len(roles) == 1 else None),
                     "team": getattr(post, "team", None), "job_type": getattr(post, "job_type", None),
                     "round": qround, "category": category(question),
                     "material": material, "evidence": evidence, "attribution": "explicit" if qcompany else "unknown",
                     "interview_date": interview_date, "published_at": post.published_at})
    return {"headline": headline, "material": material, "material_label": MATERIAL_LABELS[material],
            "interview_company": company, "body_companies": body_companies, "comment_companies": companies_in(comments),
            "rounds": rounds, "questions": rows}


def sync_questions(s: Session, post_ids: list[int] | None = None, *, skip_locked: bool = False) -> dict[str, int]:
    """稳定 key 保留题号，不调模型。按帖更新，在线同步可以跳过正在抽取的帖子。"""
    acquire_knowledge_write(s)
    s.flush()
    stmt = select(Post, RawItem).execution_options(populate_existing=True).join(RawItem, RawItem.id == Post.raw_item_id)
    if post_ids is not None:
        stmt = stmt.where(Post.id.in_(post_ids))
    rows = s.execute(stmt.with_for_update(of=Post, skip_locked=skip_locked)).all()
    stats = {"posts": len(rows), "questions": 0, "unknown_company": 0, "study": 0,
             "skipped_posts": max(0, len(post_ids or []) - len(rows))}
    for p, r in rows:
        keys = []
        info = profile(p, r.raw_text, r.raw_json)
        p.evidence_profile = {**(p.evidence_profile or {}), **{k: v for k, v in info.items() if k != "questions"},
                              "profile_version": "evidence-v1"}
        for item in info["questions"]:
            keys.append(item["key"])
            stmt = insert(InterviewQuestion).values(item)
            s.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={k: stmt.excluded[k] for k in item if k != "key"}))
            stats["questions"] += 1
            stats["unknown_company"] += item["company"] is None
            stats["study"] += item["material"] == "study"
        # 不能按整库的旧快照删题：采集可能已经为别的帖子写入了新题。
        s.execute(delete(InterviewQuestion).where(InterviewQuestion.post_id == p.id, InterviewQuestion.key.not_in(keys)))
    return stats
