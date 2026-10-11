"""多来源对齐：岗位名归一、同岗识别、字段冲突裁决。纯函数，不连数据库、不调模型。
用例取自 10-11 标注时见过的真实写法（开发集、留出集的错例）。"""
from datetime import date, datetime

from polaris.align.match import PostingView, cluster, job_key, same_job
from polaris.align.normalize import cities, company_unit, key, split_title
from polaris.align.resolve import resolve, resolve_field
from polaris.dedup import shingle_set

INTRO = ("团队介绍：财经业务依托抖音集团的流量优势，通过抖音支付、抖音月付等产品提供金融科技服务。"
         "职位要求：本科及以上学历在读，熟悉 Python。")
DUTY_A = "1、参与设计、开发和演进基于AI的金融业务架构方案；2、参与财经金融业务相关Agent应用设计和实现，确保运行稳定性、高效性。"
DUTY_B = "1、参与构建基于AI-Agent架构的金融助手，负责智能答疑、个性化投资建议等核心功能的开发；2、基于用户行为数据构建用户需求模型。"


def pv(id: int, title: str, company: str = "字节跳动", source: str = "bytedance:campus", jd: str | None = None,
       city: str = "北京", simhash: int | None = None, **fields) -> PostingView:
    parts = split_title(title, company, source)
    return PostingView(id=id, source=source, company=company.split("（")[0], unit=parts.unit,
                       unit_key=key(parts.unit) or None, role=parts.role, role_key=parts.role_key,
                       cities=cities(city), fields=fields, first_seen=datetime(2026, 10, 1),
                       last_seen=datetime(2026, 10, id % 28 + 1), simhash=simhash,
                       unit_parts=frozenset(parts.unit_parts), shingles=shingle_set(jd) if jd else frozenset())


# 岗位名归一

def test_split_title_by_company_style():
    assert split_title("腾讯地图-Agent系统架构师", "腾讯（CSIG）", "tencent:careers").unit == "CSIG/腾讯地图"
    assert split_title("Agent开发实习生 - 泛抖音", "字节跳动", "bytedance:campus").role == "Agent开发实习生"
    t = split_title("【2027校招】Product Engineer-产品工程师（AI全栈）-企业智能方向", "小红书", "xhs:careers")
    assert (t.role, t.unit) == ("Product Engineer-产品工程师（AI全栈）", "企业智能方向")
    assert company_unit("腾讯（CSIG）") == "CSIG"


def test_split_title_drops_codes_cities_and_translations():
    a = split_title("Senior Researcher, Speech Synthesis and Multimodal LLM 107809", "腾讯（IEG）")
    b = split_title("Senior Researcher, Speech Synthesis and Multimodal LLM｜高级研究员 - 语音合成与多模态大模型", "腾讯（IEG）")
    assert a.role_key == b.role_key and a.unit_parts == b.unit_parts == ("ieg",)
    c = split_title("微信搜索-AI Infra 工程师-大模型推理方向(深圳)（广州）", "腾讯（WXG）")
    assert c.unit_parts == ("wxg", "微信搜索", "大模型推理方向")


# 同岗识别

def test_same_job_across_cities():
    jd = INTRO + DUTY_A
    ok, _ = same_job(pv(1, "AI应用开发实习生-财经业务", jd=jd, city="北京"), pv(2, "AI应用开发实习生 - 财经业务", jd=jd, city="杭州"))
    assert ok


def test_same_source_same_title_different_duties_is_two_jobs():
    ok, why = same_job(pv(1, "AI应用开发实习生-财经业务", jd=INTRO + DUTY_A), pv(2, "AI应用开发实习生-财经业务", jd=INTRO + DUTY_B))
    assert not ok and "正文不同" in why


def test_cross_source_needs_no_verbatim_text():
    # 官网和 Boss 排版不同：同名同部门、指纹接近就是同一岗位
    a = pv(1, "Agent开发实习生-泛抖音", jd=INTRO + DUTY_A, simhash=0b1010)
    b = pv(2, "Agent开发实习生-泛抖音", source="boss", jd="岗位职责 " + DUTY_A, simhash=0b1011)
    assert same_job(a, b)[0]


def test_distinguishing_word_splits_near_identical_titles():
    jd = INTRO + DUTY_A  # 腾讯 Agent Harness 几个岗正文几乎一样，岗位名只差两个字，靠区分词分开
    ok, why = same_job(pv(1, "Agent Harness 工程师", "腾讯（CSIG）", "tencent:careers", jd),
                       pv(2, "Agent Harness 研发工程师", "腾讯（CSIG）", "tencent:careers", jd))
    assert not ok and "研发" in why
    ok, _ = same_job(pv(1, "AI Agent开发实习生-App Infra", jd=jd), pv(2, "AIAgent开发实习生 - App Infra", jd=jd))
    assert ok  # 只差空格、标点


def test_units_by_segment():
    jd = INTRO + DUTY_A
    sub = same_job(pv(1, "Agent系统架构师", "腾讯（CSIG）", "tencent:careers", jd),
                   pv(2, "腾讯地图-Agent系统架构师", "腾讯（CSIG）", "tencent:careers", jd))
    assert sub[0]  # "CSIG" 是 "CSIG/腾讯地图" 的子集
    diff = same_job(pv(1, "Agent开发工程师", "腾讯（IEG）", "tencent:careers", jd),
                    pv(2, "Agent开发工程师", "腾讯（PCG）", "tencent:careers", jd))
    assert not diff[0] and diff[1] == "部门不同"
    # 部门名只是字面包含（"剪映" 和 "剪映CapCut用户增长"）：正文不像就分开
    lit = same_job(pv(1, "AI研发实习生-剪映", jd=INTRO + DUTY_A), pv(2, "AI研发实习生-剪映CapCut用户增长", jd=INTRO + DUTY_B))
    assert not lit[0]


def test_one_sided_unit_needs_similar_text():
    a = pv(1, "Agent开发工程师", "腾讯", "tencent:careers", simhash=0)
    b = pv(2, "光子-Agent开发工程师", "腾讯", "boss", simhash=(1 << 20) - 1)  # 指纹差 20 位
    ok, why = same_job(a, b)
    assert not ok and "一边没写部门" in why


def test_complete_linkage_does_not_chain():
    # 没写部门的通用岗位分别像"光子"和"天美"的同名岗位；单链接会经它把两个团队串成一组，完全链接不会
    jd = INTRO + DUTY_A
    v = [pv(1, "Agent开发工程师", "腾讯", "tencent:careers", jd),
         pv(2, "光子-Agent开发工程师", "腾讯", "tencent:careers", jd),
         pv(3, "天美-Agent开发工程师", "腾讯", "tencent:careers", jd)]
    assert same_job(v[0], v[1])[0] and same_job(v[0], v[2])[0] and not same_job(v[1], v[2])[0]
    assert [[x.id for x in g] for g in cluster(v)] == [[1, 2], [3]]


def test_cluster_is_deterministic_and_keys_stable():
    jd = INTRO + DUTY_A
    v = [pv(i, "大模型算法实习生-AI Platform", jd=jd, city=c) for i, c in enumerate(["北京", "上海", "杭州"], 1)]
    g1, g2 = cluster(v), cluster(list(reversed(v)))
    assert [[x.id for x in g] for g in g1] == [[x.id for x in g] for g in g2] == [[1, 2, 3]]
    assert job_key(g1[0]) == "字节跳动|aiplatform|大模型算法实习生"


# 字段冲突裁决

def test_trust_weighted_vote_and_provenance():
    g = [pv(1, "Agent开发实习生", source="bytedance:campus", days_per_week=4),
         pv(2, "Agent开发实习生", source="synthetic:forum", days_per_week=3),
         pv(3, "Agent开发实习生", source="synthetic:group-forward", days_per_week=3)]
    r = resolve_field(g, "days_per_week")
    assert r.value == 4 and r.rule == "可信度加权投票"  # 官网 1.0 胜过 帖子 0.5 + 群转发 0.4
    assert r.alternatives == [{"value": 3, "postings": [2, 3], "sources": ["synthetic:forum", "synthetic:group-forward"]}]


def test_tie_goes_to_most_recent():
    g = [pv(3, "AI开发实习生", days_per_week=5), pv(2, "AI开发实习生", days_per_week=4)]  # 同来源同可信度，3 号更晚采到
    r = resolve_field(g, "days_per_week")
    assert (r.value, r.rule) == (5, "得分相同，取最近采到的")


def test_resolve_takes_earliest_post_date_and_unions_cities():
    g = [pv(1, "Agent开发实习生", city="北京", posted_at=date(2026, 9, 3), deadline=None),
         pv(2, "Agent开发实习生", city="上海、北京", posted_at=date(2026, 8, 20), deadline=date(2026, 11, 30))]
    values, sources, conflicts = resolve(g)
    assert values["posted_at"] == date(2026, 8, 20) and sources["posted_at"]["rule"] == "多条挂牌取最早发布"
    assert values["deadline"] == date(2026, 11, 30) and sources["deadline"]["rule"] == "只有一处写了"
    assert values["city"] == "上海、北京" and conflicts == []
