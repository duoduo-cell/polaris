"""评分里需要读懂回答的那一小步：推荐题里，回答"真正推荐了哪些岗位"。

按字符串找编号会误判：好的回答会写"J-345 是校招，不算实习，没推荐"来说明为什么排除，这不是推荐。
所以由评审模型从回答里抽出推荐的编号（只是提到、说明排除、"如果你另有打算"的备选都不算），
再由代码按条件核对。结果按回答的指纹缓存，重算分数时不再调模型。
"""
import hashlib
import json

from pydantic import BaseModel, Field

from polaris.config import ROOT
from polaris.llm.client import LLM

CACHE = ROOT / "data" / "eval" / "judge_cache.jsonl"
PROMPT_VERSION = "judge-recommended-v1"
SYSTEM = """你在给求职助手的回答评分。读回答，列出助手真正推荐给用户去投的岗位编号（形如 J-12）。
不算推荐的：只是提到、说明为什么不推荐或排除的、"如果你另有打算/更想做别的，还可以看"这类条件不同的备选。
只列编号，按回答里出现的顺序。"""


class Recommended(BaseModel):
    ids: list[str] = Field(default_factory=list, description="推荐的岗位编号，如 J-12")


def _load() -> dict[str, list[str]]:
    if not CACHE.exists():
        return {}
    rows = [json.loads(line) for line in CACHE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["key"]: r["ids"] for r in rows}


def recommended_ids(answer: str, llm: LLM | None = None) -> list[str]:
    key = hashlib.blake2b(f"{PROMPT_VERSION}\n{answer}".encode(), digest_size=16).hexdigest()
    cache = _load()
    if key in cache:
        return cache[key]
    llm = llm or LLM()
    r = llm.extract(f"回答：\n{answer}", Recommended, purpose="bench_judge", role="judge", system=SYSTEM,
                    prompt_version=PROMPT_VERSION, temperature=0)
    ids = [i.strip().upper() for i in (r.value.ids if r.ok and r.value else [])]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "ids": ids}, ensure_ascii=False) + "\n")
    return ids
