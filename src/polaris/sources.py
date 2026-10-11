"""可读来源标签和真实 URL。绝不由模型拼链接。"""
from typing import Any
from urllib.parse import urlsplit

LABELS = {"xhs": "小红书", "maimai": "脉脉", "boss": "Boss直聘", "nowcoder": "牛客",
          "bytedance:campus": "字节招聘官网", "tencent:careers": "腾讯招聘官网", "xhs:careers": "小红书招聘官网",
          "manual": "用户提供"}


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


def source_dict(source: str, url: str | None, *, raw_id: int | None = None, fetched_at: Any = None,
                published_at: Any = None, city: str | None = None) -> dict[str, Any]:
    return {"source": source, "label": label(source), "url": safe_url(url), "raw_id": f"R-{raw_id}" if raw_id else None,
            "fetched_at": fetched_at.isoformat() if fetched_at else None,
            "published_at": published_at.isoformat() if published_at else None, "city": city,
            "access": "may_require_login" if source in ("xhs", "maimai", "boss") else "public",
            "availability": "not_checked" if safe_url(url) else "missing"}
