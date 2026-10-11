"""抽取评分：逐字段把模型抽的和标准答案比。

比较前先归一（公司按大厂名单归到同一家、城市拆成集合、标题去掉【】标签和空格），再按两种口径算：
- 准确率：两边都空也算对。
- F1：只看有值的格子。模型填了但不对或答案是空 = 多填（幻觉），答案有值模型没填对 = 漏填。
答案全是空的字段（比如真实岗位都没写薪资）算不了 F1，只看多填了几次。

笔记的答案有时本身就有两种说得通的读法，标注里可以写：
- `_alt`：{字段: [也算对的值]}，比如帖子是"招聘"还是"内推"两可；
- `_optional`：{字段: [可多可少的成员]}，用于集合字段，比如面经里顺口提到的别家公司，模型写不写都算对。
"""
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from polaris.companies import match

CITY_SPLIT = re.compile(r"[、,，/;；|\s]+|和|及")
CITY_EN = {"beijing": "北京", "shanghai": "上海", "shenzhen": "深圳", "hangzhou": "杭州", "guangzhou": "广州",
           "singapore": "新加坡", "chengdu": "成都", "nanjing": "南京", "wuhan": "武汉", "remote": "远程"}
TITLE_NOISE = re.compile(r"【[^】]*】|[\s\-—_·#()（）\[\]]+")
PAREN = re.compile(r"[（(][^）)]*[）)]")


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def norm_company(v: Any) -> str | None:
    if not v:
        return None
    big = match(str(v))
    return big.name if big else _norm_text(PAREN.sub("", str(v))) or None


def norm_title(v: Any) -> str | None:
    return TITLE_NOISE.sub("", str(v)).lower() or None if v else None


def norm_city(v: Any) -> frozenset[str] | None:
    if not v:
        return None
    parts = v if isinstance(v, list) else CITY_SPLIT.split(PAREN.sub("", str(v)))  # 括号里的补充说明不计
    out = set()
    for p in parts:
        p = str(p).strip().removesuffix("市")
        if p:
            out.add(CITY_EN.get(p.lower(), p))
    return frozenset(out) or None


def norm_date(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return v.isoformat() if isinstance(v, date) else str(v)[:10]


def norm_number(v: Any) -> float | None:
    return None if v is None or v == "" else round(float(v), 2)


def norm_plain(v: Any) -> Any:
    return None if v == "" else v


def title_equal(a: str, b: str) -> bool:
    """标题：归一后相等，或一个包含另一个且长度差不多（模型常把"- 部门"后缀省掉）。"""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return short in long_ and len(short) >= 0.6 * len(long_)


def company_equal(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 2 and (a in b or b in a))


NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "company": norm_company, "title": norm_title, "city": norm_city,
    "salary_min": norm_number, "salary_max": norm_number, "salary_unit": norm_plain, "job_type": norm_plain,
    "can_convert": norm_plain, "posted_at": norm_date, "deadline": norm_date,
    "duration_months": norm_number, "days_per_week": norm_number,
}
EQUALS: dict[str, Callable[[Any, Any], bool]] = {"title": title_equal, "company": company_equal}


def norm_companies(v: Any) -> frozenset[str] | None:
    names = {norm_company(x) for x in (v or [])} - {None}
    return frozenset(names) or None


POST_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "post_type": norm_plain, "big_companies": norm_companies, "city": norm_city, "job_type": norm_plain,
    "has_referral": norm_plain, "can_convert": norm_plain, "interview_rounds": norm_number,
    "days_per_week": norm_number, "duration_months": norm_number,
}


@dataclass
class FieldScore:
    n: int = 0
    correct: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    hallucinated: int = 0  # 答案是空，模型却填了
    examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.n if self.n else None

    @property
    def f1(self) -> float | None:
        d = 2 * self.tp + self.fp + self.fn
        return 2 * self.tp / d if self.tp + self.fn else None  # 答案全空时 F1 没意义


def score(pairs: Iterable[tuple[str, dict[str, Any], dict[str, Any] | None]],
          normalizers: dict[str, Callable[[Any], Any]] = NORMALIZERS, max_examples: int = 3) -> dict[str, FieldScore]:
    """pairs: (编号, 标准答案, 模型抽的；没抽出来是 None)。"""
    scores = {f: FieldScore() for f in normalizers}
    for key, gold, pred in pairs:
        pred = pred or {}
        alts, optional = gold.get("_alt") or {}, gold.get("_optional") or {}
        for f, norm in normalizers.items():
            g, p = norm(gold.get(f)), norm(pred.get(f))
            extra = norm(optional.get(f)) if f in optional else None
            same = any(_ok(f, a, p, extra) for a in [g, *(norm(x) for x in alts.get(f, []))])
            s = scores[f]
            s.n += 1
            s.correct += same
            if same:
                s.tp += g is not None and p is not None
            else:
                s.fp += p is not None
                s.fn += g is not None
                s.hallucinated += g is None and p is not None
                if len(s.examples) < max_examples:
                    s.examples.append({"id": key, "gold": _show(g), "pred": _show(p)})
    return scores


def _ok(f: str, a: Any, p: Any, extra: frozenset | None) -> bool:
    if extra is not None and (p is None or isinstance(p, frozenset)):
        base = a or frozenset()
        return base <= (p or frozenset()) <= base | extra
    if a is None or p is None:
        return a is None and p is None
    return EQUALS.get(f, _eq)(a, p)


def _eq(a: Any, b: Any) -> bool:
    return a == b


def _show(v: Any) -> Any:
    return sorted(v) if isinstance(v, frozenset) else v


def summary(scores: dict[str, FieldScore]) -> dict[str, Any]:
    tp = sum(s.tp for s in scores.values())
    fp = sum(s.fp for s in scores.values())
    fn = sum(s.fn for s in scores.values())
    f1s = [s.f1 for s in scores.values() if s.f1 is not None]
    n = sum(s.n for s in scores.values())
    return {"micro_f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None,
            "macro_f1": sum(f1s) / len(f1s) if f1s else None,
            "accuracy": sum(s.correct for s in scores.values()) / n if n else None,
            "hallucinated": sum(s.hallucinated for s in scores.values())}
