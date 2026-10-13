"""独立原文的事实控制例；各例故意从supported初判开始，验证能否撤销局部误判。"""
import json
from concurrent.futures import ThreadPoolExecutor

from polaris.config import get_settings
from polaris.eval.claim_verifier import verify
from polaris.eval.real_tasks import PRIVATE, VERSION, guard, original_sources
from polaris.llm.client import LLM

guard()
destination = PRIVATE / f"claim-calibration-{VERSION}"
if destination.exists():
    raise ValueError("不能覆盖已有校准记录")
destination.mkdir()
manifest = json.loads((PRIVATE / "manifest-20261012.json").read_text(encoding="utf-8"))
sources = original_sources({"R-4988"})
sources["USER"] = {"text": manifest["persona"]}
controls = [
    ("publication_vs_update", "该伦敦研究实习发表于2026年10月12日。", "R-4988", "unsupported"),
    ("required_vs_preferred", "这个研究实习本科在读符合学历要求，顶会论文只是加分项。", "R-4988", "contradicted"),
    ("unknown_user_skill", "用户亲口说过不会PyTorch。", "USER", "unsupported"),
    ("compound_partial_support", "这个岗位位于伦敦，并于2026年10月12日发布。", "R-4988", "unsupported"),
    ("explicit_requirements", "该岗位要求博士在读或刚提交博士论文，完成硕士且收到博士录取者亦可；"
     "至少一篇顶级NLP/ML论文，可做六个月全职实习。", "R-4988", "supported"),
    ("unstated_days", "这份完整JD没有明确写每周到岗天数。", "R-4988", "supported"),
]
s = get_settings()
settings = s.model_copy(update={"llm_base_url": s.embed_base, "llm_api_key": s.embed_key,
                                "llm_model_judge": "qwen3.5-plus", "llm_cache": False,
                                "llm_max_retries": 0, "llm_timeout": 90})


def check(control):
    name, quote, rid, expected = control
    audit = {"claims": [{"quote": quote, "verdict": "supported", "evidence": [{"source_id": rid}]}]}
    errors = verify(audit, sources, LLM(settings=settings), params={"extra_body": {"enable_thinking": False}}, version=VERSION)
    verdict = audit["claims"][0]["verdict"]
    row = {"control": name, "expected_verdict": expected, "verdict": verdict, "errors": errors,
           "passed": not errors and verdict == expected}
    (destination / f"{name}.json").write_text(json.dumps({"row": row, "audit": audit, "source": sources[rid]},
                                                        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(row), flush=True)
    return row


with ThreadPoolExecutor(max_workers=3) as pool:
    rows = list(pool.map(check, controls))
(destination / "results.json").write_text(json.dumps({"version": VERSION, "rows": rows}, indent=2), encoding="utf-8")
