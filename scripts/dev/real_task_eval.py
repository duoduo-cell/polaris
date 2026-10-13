"""只在 polaris_eval_tasks_<日期> 冻结副本运行。默认只跑开发集，原文不进仓库。"""
import argparse
import json
from pathlib import Path

from polaris.eval.real_tasks import PRIVATE, initialize, initialize_candidate, run, score

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--initialize", action="store_true")
parser.add_argument("--initialize-candidate", action="store_true")
parser.add_argument("--manifest", type=Path, default=PRIVATE / "manifest-20261012.json")
parser.add_argument("--output", type=Path)
parser.add_argument("--split", choices=["dev", "heldout", "test"], default="dev")
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--only", nargs="+")
parser.add_argument("--answer-model")
parser.add_argument("--generate-only", action="store_true")
parser.add_argument("--profile-mode", choices=["stored", "candidate"], default="stored")
parser.add_argument("--score-dir", type=Path)
parser.add_argument("--judge-model", default="kimi-k3")
parser.add_argument("--judge-provider", choices=["primary", "embed"], default="primary")
args = parser.parse_args()
if args.initialize_candidate:
    print(json.dumps(initialize_candidate(args.manifest), ensure_ascii=False))
elif args.initialize:
    manifest = initialize(args.manifest)
    print(json.dumps({"db": manifest["db"], "fingerprint": manifest["fingerprint"], "as_of": manifest["as_of"]}))
else:
    if args.output is None:
        parser.error("必须指定全新的 data/ 内结果目录 --output")
    result = score(args.manifest, args.score_dir, args.output, args.judge_model, args.judge_provider, args.only) if args.score_dir else run(
        args.manifest, args.output, split=args.split, repeats=args.repeats, only=args.only, answer_model=args.answer_model,
        judge_model=args.judge_model, judge_provider=args.judge_provider, generate_only=args.generate_only,
        profile_mode=args.profile_mode)
    print(json.dumps(result["summary"], ensure_ascii=False))
