"""岗位名的归一：拆出部门（事业群、业务线、团队）和岗位本身，供同岗识别用。

各家写法（2026-10 采到的数据）：
- 腾讯：公司名带事业群"腾讯（CSIG）"，岗位名常以部门开头"腾讯地图-Agent系统架构师""微信搜索-Agent算法专家"，
  有的在末尾挂编号"Coding Agent Research Intern 108212"。
- 字节：部门在末尾，"Agent开发实习生 - 泛抖音""AI Agent开发实习生-App Infra"。
- 小红书官网："【2027校招】Product Engineer-产品工程师（AI全栈）-企业智能方向"，最后一段是方向或部门。
- 腾讯海外岗：有的标题后面接中文译名"Senior Researcher, Speech Synthesis｜高级研究员 - 语音合成"，有的城市末尾挂两个括号"(深圳)（广州）"。
- 合成数据和帖子：没有固定格式。
"""
import re
from dataclasses import dataclass

TAG = re.compile(r"【[^】]*】|\[[^\]]*\]")
TRAILING_CODE = re.compile(r"\s*[#＃]?\d{5,}\s*$")
PAREN_UNIT = re.compile(r"[（(]([^）)]{1,20})[）)]\s*$")
SPLIT_DASH = re.compile(r"\s+[-—–]\s+|(?<=[一-鿿A-Za-z）)#＃])[-—–](?=[一-鿿A-Za-z（(#＃])")
CITY_SUFFIX = re.compile(r"\s*[（(](singapore|london|paris|beijing|shanghai|shenzhen|hangzhou|hong kong|tokyo|seoul|"
                         r"new york|seattle|san jose|北京|上海|深圳|杭州|广州|成都|新加坡|香港|伦敦|巴黎)[）)]\s*$", re.I)
BILINGUAL = re.compile(r"\s*[｜|]\s*")
CJK = re.compile(r"[一-鿿]")
PUNCT = re.compile(r"[\s\-—–_·#＃/／|｜:：,，.。()（）\[\]【】&＆+]+")
ROLE_WORDS = ("实习生", "工程师", "专家", "研究员", "经理", "架构师", "负责人", "intern", "engineer", "researcher",
              "scientist", "architect", "developer", "manager", "lead", "研发", "开发", "算法", "产品")


@dataclass(frozen=True)
class TitleParts:
    unit: str | None  # 部门：事业群加业务线，如 "CSIG/腾讯地图"、"Data AML"
    role: str  # 岗位本身，如 "Agent系统架构师"
    role_key: str  # 用来比对的形式：去标点、小写
    unit_parts: tuple[str, ...] = ()  # 部门逐段的比对形式，如 ("csig", "腾讯地图")


def key(text: str | None) -> str:
    return PUNCT.sub("", (text or "").lower())


def company_unit(company_name: str | None) -> str | None:
    """"腾讯（CSIG）" → "CSIG"。"""
    m = PAREN_UNIT.search(company_name or "")
    return m.group(1).strip() if m else None


def _looks_like_role(part: str) -> bool:
    low = part.lower()
    return any(w in low for w in ROLE_WORDS)


def _drop_translation(t: str) -> str:
    """"Senior Researcher｜高级研究员" → "Senior Researcher"：一边是英文、一边是中文，后一段只是译名。"""
    parts = BILINGUAL.split(t)
    if len(parts) == 2 and not CJK.search(parts[0]) and CJK.search(parts[1]):
        return parts[0]
    return t


def split_title(title: str | None, company_name: str | None = None, source: str | None = None) -> TitleParts:
    t = _drop_translation(TRAILING_CODE.sub("", TAG.sub("", title or "")).strip())
    while (stripped := CITY_SUFFIX.sub("", t)) != t:  # 末尾可能挂了不止一个城市
        t = stripped
    t = t.strip()
    parts = [p.strip() for p in SPLIT_DASH.split(t) if p and p.strip()]
    units: list[str] = []
    bu = company_unit(company_name)
    if bu:
        units.append(bu)
    if len(parts) >= 2:
        # 岗位是"像岗位名"的那段；字节、小红书官网部门在后面，腾讯部门在前面
        role_idx = next((i for i, p in enumerate(parts) if _looks_like_role(p)), 0)
        role_part = parts[role_idx]
        others = [p for i, p in enumerate(parts) if i != role_idx]
        if source == "xhs:careers" and role_idx + 1 < len(parts):
            # 小红书官网："Product Engineer-产品工程师（AI全栈）-企业智能方向"：前两段都是岗位名
            role_part = "-".join(parts[:-1])
            others = parts[-1:]
        units += others
        role = role_part
    else:
        role = t
    units = list(dict.fromkeys(u for u in units if u))
    return TitleParts(unit="/".join(units) or None, role=role, role_key=key(role),
                      unit_parts=tuple(k for u in units if (k := key(u))))


def cities(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    parts = re.split(r"[、,，/;；|\s]+|和|及", re.sub(r"[（(][^）)]*[）)]", "", value))
    return frozenset(p.strip().removesuffix("市") for p in parts if p.strip())
