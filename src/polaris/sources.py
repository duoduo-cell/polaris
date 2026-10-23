"""可读来源标签和真实 URL。绝不由模型拼链接。"""
import re
from typing import Any
from urllib.parse import urlsplit

LABELS = {"xhs": "小红书", "maimai": "脉脉", "boss": "Boss直聘", "nowcoder": "牛客",
          "bytedance:campus": "字节招聘官网", "tencent:careers": "腾讯招聘官网", "xhs:careers": "小红书招聘官网",
          "manual": "用户提供", "demo": "合成演示（非真实招聘）"}


def safe_url(value: str | None) -> str | None:
    if not value or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        return value
    except ValueError:
        return None


def label(source: str) -> str:
    return LABELS.get(source, source)


def date_kind(raw: Any) -> str:
    """原始层把发布/更新都放在published_at；给使用者保留实际口径，不能拿更新时间冒充首发。"""
    data = raw.raw_json or {}
    if raw.source in ("xhs", "maimai") and raw.published_at:
        return "published"
    if any(data.get(k) for k in ("publishTime", "publish_time", "publishDate", "postedOn", "datePosted")):
        return "published"
    body = raw.raw_text or ""
    if re.search(r"^(?:发布日期|发布时间|发布于|发布|Posted on)\s*[：:]", body, re.M | re.I):
        return "published"
    if data.get("LastUpdateTime") or re.search(r"^(?:更新时间|更新于|Last updated)\s*[：:]", body, re.M | re.I):
        return "updated"
    return "unknown"


def source_dict(source: str, url: str | None, *, raw_id: int | None = None, fetched_at: Any = None,
                published_at: Any = None, city: str | None = None) -> dict[str, Any]:
    return {"source": source, "label": label(source), "url": safe_url(url), "raw_id": f"R-{raw_id}" if raw_id else None,
            "fetched_at": fetched_at.isoformat() if fetched_at else None,
            "published_at": published_at.isoformat() if published_at else None, "city": city,
            "access": "may_require_login" if source in ("xhs", "maimai", "boss") else "public",
            "availability": "not_checked" if safe_url(url) else "missing"}
