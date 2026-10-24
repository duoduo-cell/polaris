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
        self.mouse = SimpleNamespace(wheel=lambda *a: None, move=lambda *a: None)
        self.request = SimpleNamespace(get=lambda url, **k: FakeResponse())

    def locator(self, selector):
        return SimpleNamespace(bounding_box=lambda **k: {"x": 900, "y": 100, "width": 400, "height": 700})


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


def test_post_extraction_and_question_evidence_see_the_image_text():
    """10-12：R-997 九张图约 7,000 字面经，抽取只读前 6,000 字（图片文字排在评论后面被截掉），同步题目时也只把标题和描述当正文。"""
    from polaris.extract import post_input
    from polaris.interviews import split_text

    text = ("标题\n\n正文见图\n\n评论：\n" + "\n".join(f"- 评论{i}" * 20 for i in range(80))
            + "\n\n图片里的文字：\n[图1]\n一面：讲一下 PPO 的目标函数")
    assert "讲一下 PPO 的目标函数" in post_input(text) and "讲一下 PPO" not in text[:6000]
    _, body, comments = split_text(text, {"note": {"title": "标题", "desc": "正文见图"}, "comments": [{"content": "评论0"}]})
    assert "讲一下 PPO 的目标函数" in body and "PPO" not in comments


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


def test_the_same_person_posting_twice_is_counted_once(db):
    """10-12 抽查：同一个人隔天发两条求内推，被记成了两个人。"""
    with session_scope() as s:
        for days in (1, 2):
            text = f"同一个人第 {days} 条"
            raw = RawItem(source="xhs", tier="B", kind="post", raw_text=text, content_hash=content_hash(text), fetched_at=NOW,
                          published_at=NOW - timedelta(days=days), raw_json={"note": {"user": {"userId": "u1"}}})
            s.add(raw)
            s.flush()
            s.add(PeerProfile(raw_item_id=raw.id, source="xhs", published_at=raw.published_at, who="作者", stage="硕士",
                              papers=[], internships=[], awards=[], target_company="字节跳动", big_company="字节跳动",
                              job_type="日常实习", direction="Agent", outcome="进行中", evidence=text, prompt_version="test"))
    out = peers_search(PeersSearchArgs(company="字节"))
    assert out["summary"]["matched"] == 1 and out["items"][0]["days_ago"] == 1  # 留最新的那条


def test_not_stating_internships_is_not_zero_internships(db):
    """10-12 真实问答：工具把"没写实习"返回成空列表，模型说成了"0 实习拿到 offer"（R-997 原文只写了学历）。"""
    with session_scope() as s:
        for i, evidence in enumerate(("bg:双非本，985硕，通信工程转码", "28届研二0实习，拿到 Agent 日常实习",
                                      "长沙作业帮有没有实习岗可以内推")):
            raw = RawItem(source="xhs", tier="B", kind="post", raw_text=evidence, content_hash=content_hash(evidence),
                          fetched_at=NOW, published_at=NOW - timedelta(days=i))
            s.add(raw)
            s.flush()
            s.add(PeerProfile(raw_item_id=raw.id, source="xhs", published_at=raw.published_at, who="作者", stage="硕士",
                              papers=[], internships=[], awards=[], target_company="阿里巴巴", big_company="阿里巴巴",
                              job_type="日常实习", direction="Agent", outcome="拿到offer", evidence=evidence, prompt_version="test"))
    out = peers_search(PeersSearchArgs(company="阿里"))
    said = [x["internships"] for x in out["items"]]
    assert said[0].startswith("没写") and said[1] == "原句写了没有实习" and said[2].startswith("没写")
    assert out["items"][0]["papers"].startswith("没写") and out["summary"]["internships_not_stated"] == 2
    assert out["summary"]["explicit_no_internships"] == 1 and out["summary"]["internships_median"] == 0


def test_posts_without_people_are_not_read_again_every_round(db, monkeypatch):
    """10-12：没抽出人的帖子不留记录，常驻采集每轮都会把它当成没抽过再抽一遍。"""
    import polaris.peers as peers

    calls = []

    class FakeLLM:
        def extract(self, *a, **k):
            calls.append(1)
            return SimpleNamespace(ok=True, value=PeerFacts(people=[]), call_ids=[None])

    monkeypatch.setattr(peers, "LLM", FakeLLM)
    with session_scope() as s:
        text = "求问字节 Agent 实习一面一般问什么"
        s.add(RawItem(source="xhs", tier="B", kind="post", raw_text=text, content_hash=content_hash(text), fetched_at=NOW,
                      published_at=NOW))
    assert peers.run(10)["read"] == 1 and peers.run(10)["read"] == 0 and len(calls) == 1


def test_official_daily_is_not_repeated_after_a_restart(db):
    """10-12：常驻采集重启后忘了今天已经整批采过官网，又采了 35 分钟。现在查库。"""
    from polaris.daemon import BEIJING, official_done_today
    from polaris.db.models import Run

    now = datetime.now(BEIJING)
    assert not official_done_today(now)
    with session_scope() as s:
        for source in ("tencent", "bytedance"):
            s.add(Run(kind="collect", params={"source": source, "mode": "official-daily"}, status="ok", stats={}))
    assert not official_done_today(now)  # 还差小红书招聘官网
    with session_scope() as s:
        s.add(Run(kind="collect", params={"source": "xhs_careers", "mode": "official-daily"}, status="ok", stats={}))
    assert official_done_today(now)


def test_failed_official_source_is_not_marked_done_or_retried_during_cooldown(db):
    from datetime import timedelta

    from polaris.daemon import BEIJING, OFFICIAL_RETRY_COOLDOWN, official_done_today, official_pending_sources
    from polaris.db.models import Run

    # 固定在当天中午：原来用当前时刻，晚上 23 点后“再过一小时”跨到第二天，腾讯又变成待采（10-24 23:13 失败过）
    now = datetime.now(BEIJING).replace(hour=12, minute=0, second=0, microsecond=0)
    with session_scope() as s:
        s.add(Run(kind="collect", params={"source": "tencent", "mode": "official-daily"}, status="ok", stats={},
                  started_at=now - timedelta(minutes=20)))
        s.add(Run(kind="collect", params={"source": "bytedance", "mode": "official-daily"}, status="failed",
                  stats={}, started_at=now - timedelta(minutes=10)))

    assert not official_done_today(now)
    assert official_pending_sources(now) == {"xhs_careers"}
    assert official_pending_sources(now + OFFICIAL_RETRY_COOLDOWN) == {"bytedance", "xhs_careers"}


def test_ocr_html_and_code_fences_are_stripped():
    raw = ("```html\n<html><body>\n<h2>个人背景</h2>\n<p>学历：双非本 9 硕</p>\n"
           "<table><tr><td>一面</td><td>PPO</td></tr></table>\n</body></html>\n```")
    out = xhs_deep.clean_ocr(raw)
    assert "<" not in out and "```" not in out and "学历：双非本 9 硕" in out and "一面 PPO" in out


def test_offer_cohort_excludes_unknown_employers_and_non_internships(db):
    with session_scope() as s:
        for i, (company, job_type, days) in enumerate((("腾讯", "日常实习", 1), ("腾讯", "暑期实习", 2),
                                                     ("腾讯", "校招", 3), (None, "日常实习", 4),
                                                     ("腾讯", "日常实习", -1))):
            raw = RawItem(source="xhs", tier="B", kind="post", raw_text=f"合成背景{i}", content_hash=f"cohort-{i}",
                          fetched_at=NOW, published_at=NOW - timedelta(days=days))
            s.add(raw)
            s.flush()
            s.add(PeerProfile(raw_item_id=raw.id, source="xhs", published_at=raw.published_at,
                              who="作者", stage="未说明", papers=[], internships=[], awards=[],
                              target_company=company, big_company=company, job_type=job_type,
                              direction="Agent", outcome="拿到offer", evidence=f"合成背景{i}", prompt_version="test"))
    out = peers_search(PeersSearchArgs(direction="Agent", job_type="实习", outcome="拿到offer"))
    assert out["summary"]["matched"] == 2 and out["returned"] == 2
    assert {p["job_type"] for p in out["items"]} == {"日常实习", "暑期实习"}
    assert out["summary"]["stage_not_stated"] == 2 and out["summary"]["papers_not_stated"] == 2
    assert out["scope"]["excluded_unknown_company_records"] == 1
    assert out["scope"]["job_type"] == "实习" and out["scope"]["from_date"]
    assert peers_search(PeersSearchArgs(job_type="实习", big_only=False))["summary"]["matched"] == 3
