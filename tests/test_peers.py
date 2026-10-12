"""人和结果（10-12）：深挖小红书笔记（读图、翻评论）、从帖子里抽"谁拿到了什么、什么背景"、按公司和阶段查。全部合成数据。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from polaris.collectors import xhs_deep
from polaris.db.models import PeerProfile, RawItem
from polaris.db.session import session_scope
from polaris.ingest import content_hash
from polaris.peers import Paper, PeerFacts, Person, compose, extract_one
from polaris.tools.peers import PeersSearchArgs, peers_search

NOW = datetime.now(UTC)


class FakeResponse:
    ok = True
    headers = {"content-type": "image/webp"}

    def body(self):
        return b"fake-image"


class FakePage:
    def __init__(self):
        self.mouse = SimpleNamespace(wheel=lambda *a: None)
        self.request = SimpleNamespace(get=lambda url, **k: FakeResponse())


class FakeCaller:
    """像采集浏览器：往下翻一次评论多出 3 条，之后不再增加。"""

    def __init__(self):
        self.page = FakePage()
        self.sleep = lambda s: None
        self.scrolls = 0

    def _eval_json(self, expr, arg=None):
        self.scrolls += 1
        return {"comments": {"list": [{"content": f"评论{i}", "likeCount": i} for i in range(5)], "hasMore": True}}


def test_enrich_reads_images_and_more_comments(monkeypatch):
    monkeypatch.setattr(xhs_deep, "_ocr", lambda images, run_id=None: ["本科 华科，ACL 一作 1 篇，字节 Seed 实习 OC", ""])
    note = {"noteId": "n1", "title": "上岸复盘", "desc": "见图", "type": "normal", "time": int(NOW.timestamp() * 1000),
            "imageList": [{"urlDefault": "https://img/1"}, {"urlDefault": "https://img/2"}]}
    text, raw_json, stats = xhs_deep.enrich(FakeCaller(), "n1", note, {"list": [{"content": "评论0"}, {"content": "评论1"}]})
    assert stats["images"] == 2 and stats["images_read"] == 2 and stats["images_with_text"] == 1  # 第二张没字
    assert stats["comments"] == 5  # 第一页 2 条，翻了一次变 5 条，再翻不涨就停
    assert "图片里的文字：\n[图1]\n本科 华科" in text and "[图2]" not in text
    assert raw_json["images"] == [{"i": 1, "ocr": "本科 华科，ACL 一作 1 篇，字节 Seed 实习 OC"}]
    assert "nickname" not in str(raw_json["comments"])  # 评论只留内容、点赞、时间、属地


def test_brief_and_compose_keep_image_text_when_the_body_is_long():
    text = "标题\n\n正文" + "很长" * 3000 + "\n\n评论：\n- 我也面了\n\n图片里的文字：\n[图1]\n上交硕士 2 篇 CCF-A"
    assert "上交硕士" in xhs_deep.brief(text, 2400) and "我也面了" in xhs_deep.brief(text, 2400)
    raw = RawItem(source="xhs", tier="B", kind="post", raw_text=text)
    assert "上交硕士" in compose(raw)


def test_extract_one_counts_top_papers_and_normalizes_the_company():
    facts = PeerFacts(people=[Person(who="作者", stage="硕士", school="上交", papers=[Paper(level="CCF-A"), Paper(level="CCF-B")],
                                     target_company="豆包", outcome="拿到offer", evidence="上交硕士，两篇论文，豆包 OC")])
    llm = SimpleNamespace(extract=lambda *a, **k: SimpleNamespace(ok=True, value=facts, call_ids=[None]))
    raw = RawItem(id=1, source="xhs", tier="B", kind="post", raw_text="x", published_at=NOW)
    row = extract_one(llm, raw)[0]
    assert row["top_papers"] == 1 and row["big_company"] == "字节跳动" and row["school"] == "上交"


def _seed():
    rows = [("字节跳动", "硕士", "拿到offer", 3, [{"level": "CCF-A"}], [{"company": "美团"}, {"company": "阿里"}], "Agent"),
            ("字节跳动", "本科", "挂了", 10, [], [{"company": "快手"}], "Agent"),
            ("字节跳动", "博士", "拿到offer", 400, [{"level": "CCF-A"}], [], "后训练"),  # 超过半年
            ("腾讯", "硕士", "拿到offer", 5, [], [], "Agent")]
    with session_scope() as s:
        for company, stage, outcome, days, papers, internships, direction in rows:
            text = f"{company} {stage} {outcome} {days}"
            raw = RawItem(source="xhs", tier="B", kind="post", raw_text=text, content_hash=content_hash(text), fetched_at=NOW,
                          url="https://www.xiaohongshu.com/explore/x", published_at=NOW - timedelta(days=days))
            s.add(raw)
            s.flush()
            s.add(PeerProfile(raw_item_id=raw.id, source="xhs", published_at=raw.published_at, who="作者", stage=stage,
                              papers=papers, top_papers=sum(1 for p in papers if p["level"] == "CCF-A"), internships=internships,
                              awards=[], target_company=company, big_company=company, job_type="日常实习", direction=direction,
                              outcome=outcome, evidence=f"{stage}，{outcome}", prompt_version="test"))


def test_peers_search_filters_by_company_and_time_and_says_how_many(db):
    _seed()
    out = peers_search(PeersSearchArgs(company="字节", direction="Agent"))
    sm = out["summary"]
    assert sm["matched"] == 2 and sm["by_outcome"]["拿到offer"] == 1 and sm["by_outcome"]["挂了"] == 1  # 400 天前那条不算
    assert sm["with_top_paper"] == 1 and sm["internships_median"] == 1.5
    assert out["items"][0]["days_ago"] == 3 and out["items"][0]["id"].startswith("R-")  # 最新的在前，能点回原帖
    assert "样本太少" in out["hint"]
    assert peers_search(PeersSearchArgs(company="字节", since_days=730, stage="博士"))["summary"]["matched"] == 1
    assert "放宽条件" in peers_search(PeersSearchArgs(company="美团"))["hint"]
