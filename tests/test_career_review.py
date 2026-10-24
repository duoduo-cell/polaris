"""HR 视角与同类样本对照：硬门槛按岗位数统计、缺的信息列成要问的、样本少直说、学校只粗分。"""
from datetime import UTC, datetime, timedelta

import pytest

from polaris.career_review import compare, github_evidence, my_school_bucket, review, school_bucket
from polaris.db.models import Job, PeerProfile, RawItem
from polaris.db.session import session_scope
from polaris.ingest import content_hash
from polaris.memory.update import Candidate, Context, apply

NOW = datetime.now(UTC)


@pytest.mark.parametrize(("text", "bucket"), [
    ("2本9硕", "写了 985/211 类"), ("211本", "写了 985/211 类"), ("清华本", "写了 985/211 类"),
    ("非92硕", "写了双非/非 92 类"), ("双非一本", "写了双非/非 92 类"), ("QS前50", "海外"),
    ("某某大学", "写了但没法归类"), ("", "没写"), (None, "没写"), ("未说明", "没写"),
])
def test_school_bucket_only_reads_what_the_post_says(text, bucket):
    assert school_bucket(text) == bucket


def test_own_school_only_recognised_by_the_985_list():
    assert my_school_bucket("哈工大") == my_school_bucket("哈尔滨工业大学") == "写了 985/211 类"
    assert my_school_bucket("某理工学院") == "未归类（只认 985 名单）"


def seed_jobs(s):
    rows = [("ok", 4, ["熟悉 Python"]), ("five", 5, ["熟悉 Python"]), ("master", 4, ["硕士及以上学历在读", "熟悉 Python", "了解评测"]),
            ("unknown", None, ["熟悉 Python"])]
    out = {}
    for key, days, reqs in rows:
        j = Job(key=f"rv-{key}", company="合成科技", company_name="合成科技", title=f"Agent 实习 {key}", city="北京", cities=["北京"],
                job_type="intern", status="active", directions=["Agent"], days_per_week=days, requirements=reqs,
                jd_text=" ".join(reqs), first_seen_at=NOW - timedelta(days=3), last_seen_at=NOW, is_synthetic=False)
        s.add(j)
        s.flush()
        out[key] = j.id
    return out


def test_review_counts_hard_gates_per_job_and_lists_what_to_ask(db):
    with session_scope() as s:
        for attr, value in [("degree", "本科在读"), ("skill", "Python"), ("project", "做了个 RAG 问答 github.com/someone/rag-demo")]:
            apply(Context(s, NOW, "合成用户"), Candidate(attr, value, f"合成用户{value}"))
        ids = seed_jobs(s)
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/contents"):
            return [{"name": "README.md"}, {"name": "tests"}, {"name": "src"}]
        return {"html_url": "https://github.com/someone/rag-demo", "language": "Python", "stargazers_count": 3,
                "pushed_at": "2026-10-20T00:00:00Z", "archived": False, "description": "demo"}

    criteria = {"city": "北京", "job_type": "intern", "direction": "Agent", "max_days_per_week": 4, "big_only": False}
    with session_scope() as s:
        out = review(s, criteria, fetch_github=fetch)
    q = out["qualification"]
    # 每周 5 天的岗位被范围条件筛掉（不进范围）；要硕士的那个算学历硬门槛；没写天数的进范围、不算冲突
    assert q["hard_gates"] == {"学历": 1} and q["gate_examples"]["学历"] == [f"J-{ids['master']}"]
    assert set(q["no_conflict_examples"]) == {f"J-{ids['ok']}", f"J-{ids['unknown']}"}
    assert q["unknown_checks"] == {"每周天数": 1}
    gh = out["evidence"]["projects"][0]["github"]
    assert gh["ok"] and gh["has_readme"] and gh["has_tests"] and gh["language"] == "Python"
    asked = {x["field"] for x in out["ask_user"]}
    assert {"days_per_week", "duration_months", "internship_experience", "paper_experience"} <= asked
    assert "project" not in asked
    assert github_evidence("github.com/someone/rag-demo", fetch) == gh and len(calls) == 2  # 一小时内不重复读


def test_github_failure_is_reported_not_raised():
    def broken(url):
        raise TimeoutError("timeout")

    out = github_evidence("https://github.com/nobody/never-cached-repo", broken)
    assert out == {"repo": "nobody/never-cached-repo", "ok": False, "error": "读不到：TimeoutError"}


def peer(s, n, *, company="合成科技", outcome="拿到offer", stage="硕士", school=None, internships=(), job_type="日常实习"):
    text = f"同类样本 {n} {company} {outcome}"
    raw = RawItem(source="maimai", tier="B", kind="post", raw_text=text, content_hash=content_hash(text), fetched_at=NOW)
    s.add(raw)
    s.flush()
    s.add(PeerProfile(raw_item_id=raw.id, source="maimai", published_at=NOW - timedelta(days=n + 1), who="作者", stage=stage,
                      school=school, internships=[{"company": c} for c in internships], big_company=company,
                      target_company=company, target_role="Agent 开发实习", job_type=job_type, direction="Agent",
                      outcome=outcome, evidence=text, prompt_version="t"))


def test_compare_layers_single_out_offers_and_admit_small_samples(db):
    with session_scope() as s:
        apply(Context(s, NOW, "合成用户"), Candidate("degree", "本科在读", "本科在读"))
        apply(Context(s, NOW, "合成用户"), Candidate("school", "哈工大", "哈工大"))
        for n in range(3):  # 同公司拿到 offer 的只有 3 个
            peer(s, n, school="2本9硕", internships=("某大厂",))
        for n in range(3, 9):  # 别家同方向拿到 offer 的 6 个
            peer(s, n, company="别家科技", school=None if n % 2 else "双非一本", internships=() if n % 2 else ("小厂", "中厂"))
        peer(s, 20, outcome="挂了", stage="本科", school="双非一本")
    with session_scope() as s:
        out = compare(s, company="合成科技", direction="Agent", job_type="实习")
    layers = {x["layer"]: x for x in out["layers"]}
    same = layers["同公司、同方向、同类型"]
    assert same["offer"]["people"] == 3 and same["all"]["people"] == 4 and same["enough"] is False
    assert out["reading"][0].startswith("同公司、同方向、同类型：拿到 offer 的自述只有 3 人，样本太少")
    wide = layers["同方向、同类型（各大厂）"]["offer"]
    assert wide["people"] == 9 and wide["school"]["写了 985/211 类"] == 3 and wide["school"]["没写"] == 3
    assert wide["internships_known"] == 6 and wide["internships_median"] == 1.5  # 3 个 1 段 + 3 个 2 段；没写的不算 0
    assert out["you"]["school_bucket"] == "写了 985/211 类" and out["you"]["stage"] == "本科在读"
