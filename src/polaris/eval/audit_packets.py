"""裁判输入去重：原始证据完整保留一次，工具摘要不代替独立证据。"""
import json
from typing import Any

from polaris.results import REF_RE, refs_from_output

TOOL_BODY_FIELDS = {"jd_text", "text", "raw_text", "raw_json", "evidence", "advice"}


def review_refs(answer: str, calls: list[dict]) -> set[str]:
    """引用、实际深读与背景统计所需的原文；另保留所有排除实例，不送未引用的搜索候选正文。"""
    refs = set(REF_RE.findall(answer))
    for call in calls:
        if not call.get("ok"):
            continue
        output = call.get("output") or ""
        if call["tool"] in ("jobs_get", "posts_get", "peers_search", "source_open"):
            refs.update(refs_from_output(output))
        elif call["tool"] == "jobs_search":
            try:
                data = json.loads(output)
            except (ValueError, TypeError):
                continue
            refs.update(refs_from_output(json.dumps({"excluded_examples": data.get("excluded_examples", [])})))
    return refs


def without_duplicate_body(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_duplicate_body(v) for k, v in value.items() if k not in TOOL_BODY_FIELDS}
    if isinstance(value, list):
        return [without_duplicate_body(v) for v in value]
    return value


def review_packet(packet: dict) -> dict:
    """proof_refs是完整原文/元数据；不再次发送正文副本或工具里的同一段正文。"""
    if not packet.get("proof_refs"):
        raise ValueError("没有独立原文，不能用工具摘要冒充")
    view = {k: v for k, v in packet.items() if k not in ("original_sources", "source_lines", "dialogue", "tool_calls")}
    view["tool_calls"] = [{**c, "output": without_duplicate_body(c.get("output"))} for c in packet["tool_calls"]]
    view["evidence_policy"] = "完整独立原始证据只在proof_refs；工具结果去除重复正文，摘要/抽取字段不作为事实证据"
    return view


def encode_review(packet: dict) -> str:
    return json.dumps(review_packet(packet), ensure_ascii=False, default=str)
