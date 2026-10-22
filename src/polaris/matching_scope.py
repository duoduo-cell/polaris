"""从已校验的工具参数保留本次条件；不从来源原文猜条件，也不修改长期画像。"""
from dataclasses import replace
from typing import Any

from polaris.proactive.decide import Profile


def criteria(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("city", "direction"):
        value = values.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= 100:
            result[key] = value.strip()
    if values.get("job_type") in ("intern", "campus", "social"):
        result["job_type"] = values["job_type"]
    days = values.get("max_days_per_week")
    if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 7:
        result["max_days_per_week"] = days
    return result


def scoped_profile(profile: Profile | None, scope: dict[str, Any]) -> Profile | None:
    if not scope:
        return profile
    changes = {}
    if "city" in scope:
        changes["cities"] = [scope["city"]]
    if "direction" in scope:
        changes["roles"] = [scope["direction"]]
    if "job_type" in scope:
        changes["job_type"] = scope["job_type"]
    if "max_days_per_week" in scope:
        changes["days_per_week"] = scope["max_days_per_week"]
    return replace(profile or Profile(), **changes)


def scope_note(scope: dict[str, Any]) -> str:
    fields = [scope.get("city"), scope.get("direction"),
              {"intern": "实习", "campus": "校招", "social": "社招"}.get(scope.get("job_type")),
              f"每周最多{scope['max_days_per_week']}天" if scope.get("max_days_per_week") else None]
    return "按本次搜索条件：" + "、".join(str(x) for x in fields if x) + "；其他条件沿用已记录信息，不改变长期偏好。"
