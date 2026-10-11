from polaris.eval.scoring import norm_city, norm_company, norm_title, score, summary


def test_normalizers():
    assert norm_company("腾讯（CSIG）") == norm_company("Tencent") == "腾讯"
    assert norm_company("穹宇集团（北京）") == "穹宇集团"
    assert norm_city("北京市，上海市") == norm_city(["上海", "北京"]) == frozenset({"北京", "上海"})
    assert norm_city("Singapore") == frozenset({"新加坡"})
    assert norm_title("【2027校招】研发工程师-安全技术") == norm_title("研发工程师 - 安全技术")


def test_score_counts_hallucination_and_miss():
    gold = {"company": "字节跳动", "title": "Agent开发实习生 - 质量技术", "city": "北京", "days_per_week": 4,
            "salary_min": None, "job_type": "intern"}
    pred = {"company": "字节", "title": "Agent开发实习生", "city": "北京", "days_per_week": None,
            "salary_min": 300, "job_type": "intern"}
    s = score([("x", gold, pred)])
    assert s["company"].tp == 1 and s["title"].tp == 1 and s["city"].tp == 1
    assert s["days_per_week"].fn == 1 and s["days_per_week"].correct == 0
    assert s["salary_min"].hallucinated == 1 and s["salary_min"].f1 is None
    assert s["deadline"].correct == 1  # 两边都空
    assert summary(s)["hallucinated"] == 1


def test_missing_prediction_counts_as_all_miss():
    s = score([("x", {"company": "腾讯", "city": "深圳"}, None)])
    assert s["company"].fn == 1 and s["city"].fn == 1 and s["company"].f1 == 0


def test_alt_and_optional():
    from polaris.eval.scoring import POST_NORMALIZERS

    gold = {"post_type": "招聘", "big_companies": ["滴滴"], "has_referral": False,
            "_alt": {"post_type": ["内推"]}, "_optional": {"big_companies": ["字节跳动"]}}
    ok = score([("a", gold, {"post_type": "内推", "big_companies": ["滴滴", "字节跳动"], "has_referral": False})],
               POST_NORMALIZERS)
    assert ok["post_type"].correct == 1 and ok["big_companies"].correct == 1 and ok["has_referral"].tp == 1
    bad = score([("b", gold, {"post_type": "面经", "big_companies": ["字节跳动"]})], POST_NORMALIZERS)
    assert bad["post_type"].correct == 0 and bad["big_companies"].correct == 0
