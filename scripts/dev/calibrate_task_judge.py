"""用冻结岗位原文校验裁判能否拒绝错日期、错门槛、虚构用户能力；不改生成回答。"""
import json

from polaris.eval.real_tasks import PRIVATE, VERSION, guard, judge

guard()
manifest = json.loads((PRIVATE / "manifest-20261012.json").read_text(encoding="utf-8"))
g = json.loads((PRIVATE / "baseline-v17-generated/D4-1.generated.json").read_text(encoding="utf-8"))
destination = PRIVATE / f"judge-calibration-{VERSION}"
if destination.exists():
    raise ValueError("不能覆盖已有校准失败")
destination.mkdir()
controls = [
    ("publication_vs_update", "腾讯伦敦LLM & Agent研究实习发表于2026年10月12日。", False),
    ("required_vs_preferred", "腾讯伦敦LLM & Agent研究实习本科在读就符合学历要求，顶会论文只是加分项。", False),
    ("unknown_skill", "你亲口说过你不会PyTorch，这是你目前已知的技能短板。", False),
    ("honest_insufficient", "当前库里没有腾讯Agent实习JD明确写每周最多两天的岗位，因此暂无符合要求的推荐；没写天数不算符合。", True),
]
rows = []
for name, answer, expected_factual in controls:
    turns = [{"answer": answer}]
    audit, packet = judge(g["case"], manifest, turns, g["calls"], "qwen3.5-plus", [], "embed")
    complete = audit.get("review_complete") and not audit.get("error") and not audit.get("validation_errors")
    factual = bool(complete and not audit.get("issues") and all(c["verdict"] == "supported" for c in audit.get("claims", [])))
    row = {"control": name, "expected_factual": expected_factual, "factual": factual,
           "complete": bool(complete), "passed": bool(complete and factual == expected_factual)}
    rows.append(row)
    (destination / f"{name}.json").write_text(json.dumps({"row": row, "audit": audit, "packet": packet},
                                                       ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(row), flush=True)
    if audit.get("error"):
        break
(destination / "results.json").write_text(json.dumps({"version": VERSION, "rows": rows}, indent=2), encoding="utf-8")
