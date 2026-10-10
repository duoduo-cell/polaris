"""合成岗位数据。

事实由代码定（固定随机种子），模型只负责把事实写成招聘文本；写完用代码核对：
给定的事实短语必须原样出现，没给的信息不许出现。所以每条的标准答案在生成时就确定了。

全部用虚构公司名，网址用保留域名 example.com，不冒用任何真实公司。
有意埋三类难例：同一岗位在两个来源各发一次且字段矛盾（M3 去重）、标题不含方向关键词的冷门岗位
（岗位发现）、缺字段的岗位（抽不到要留空）。另有一批无关岗位做反例。
"""
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from polaris.config import ROOT
from polaris.llm import LLM, parallel_map
from polaris.schemas import JobFacts

SEED = 20261010
PROMPT_VERSION = "synth-jd-v1"
FIXTURE = ROOT / "eval" / "fixtures" / "synth_jobs.jsonl"
FETCHED_AT = "2026-10-10T09:00:00+08:00"

# (公司, 是否大厂, 语言)。全部虚构。
COMPANIES = [
    ("穹宇集团", True, "zh"), ("星槎科技", True, "zh"), ("九衢互联", True, "zh"), ("曜石网络", True, "zh"),
    ("青梧智能", False, "zh"), ("砚池数据", False, "zh"), ("鹿鸣医疗AI", False, "zh"),
    ("浮光生物计算", False, "zh"), ("松间金融科技", False, "zh"), ("白榆机器人", False, "zh"),
    ("听澜教育", False, "zh"), ("铁砧芯片", False, "zh"),
    ("Northwind Labs", False, "en"), ("Lumen Quarry AI", False, "en"),
]
DIRECTIONS = {
    "大模型应用": (["大模型应用开发实习生", "AI 应用开发实习生", "LLM 应用工程实习生"],
              "基于大模型搭建业务应用，做提示词工程和效果优化"),
    "RAG": (["RAG 算法实习生", "检索增强生成研发实习生", "知识库问答开发实习生"],
            "检索增强生成：文档解析、向量检索、重排和问答效果评估"),
    "Agent": (["Agent 开发实习生", "智能体研发实习生", "AI Agent 工程实习生"],
              "Agent 的工具调用链路、多步规划和任务执行"),
    "模型评测": (["大模型评测实习生", "模型评估算法实习生"], "大模型评测集建设、自动化评测和结果分析"),
    "数据": (["大模型数据工程实习生", "训练数据开发实习生"], "大模型训练数据的采集、清洗和质量评估"),
    "多模态": (["多模态算法实习生"], "图文多模态模型的数据构建和训练"),
    "推理加速": (["大模型推理优化实习生"], "大模型推理服务的性能优化和部署"),
}
EN_DIRECTIONS = {
    "Agent": ("AI Agent Engineering Intern", "tool calling, multi-step planning and agent evaluation"),
    "RAG": ("Retrieval & RAG Research Intern", "retrieval pipelines, reranking and grounded QA"),
    "模型评测": ("LLM Evaluation Intern", "building eval sets and automated evaluation harnesses"),
    "大模型应用": ("Applied LLM Intern", "shipping LLM-powered product features"),
}
# 冷门岗位：标题里没有方向关键词，正文其实相关。(标题, 方向, 正文要讲的事)
COLD = [
    ("生物数据集质检实习生", "模型评测", "生物垂直领域的大模型评测数据集建设和质检"),
    ("医学文本标注规范实习生", "数据", "为医疗大模型制定标注规范、构建微调数据"),
    ("金融研究助理（技术方向）", "RAG", "搭建研报检索问答系统，优化检索和回答质量"),
    ("客服系统优化实习生", "Agent", "用大模型 Agent 重构客服工单的自动处理流程"),
    ("教研工具开发实习生", "大模型应用", "基于大模型的智能出题和作文批改工具"),
    ("测试开发实习生（AI 平台）", "模型评测", "为大模型平台搭建自动化评测流水线"),
    ("搜索质量实习生", "RAG", "向量检索和重排效果的分析与改进"),
    ("机器人交互实习生", "多模态", "机器人上的语音和视觉多模态交互"),
    ("芯片软件栈实习生", "推理加速", "大模型推理在自研芯片上的适配和性能优化"),
    ("内容安全策略实习生", "模型评测", "大模型输出的安全评测和红队测试"),
]
NEGATIVE = [
    ("前端开发实习生", "Web 前端页面开发", "intern"), ("新媒体运营实习生", "公众号和短视频运营", "intern"),
    ("财务实习生", "日常报销和账务整理", "intern"), ("销售管培生", "企业客户销售", "campus"),
    ("Java 后端实习生", "订单系统的 Java 后端开发", "intern"), ("行政助理实习生", "办公室行政事务", "intern"),
]
CITIES = ["北京", "上海", "杭州", "深圳", "广州", "成都", "武汉", "哈尔滨", "远程"]
EN_CITIES = ["Shanghai", "Beijing", "Singapore", "Remote"]
STYLES = {
    "official": ("synthetic:official", "公司招聘官网的岗位详情页：岗位名称、工作地点、岗位职责、任职要求分段列出"),
    "article": ("synthetic:article", "公众号招聘推文里的一段：开头一两句介绍团队，语气轻松"),
    "forward": ("synthetic:group-forward", "微信群里转发的内推消息：很短，口语化，信息挤在几行里"),
    "forum": ("synthetic:forum", "求职论坛里的招聘帖：一个标题加正文，带一点内推人的口吻"),
}
EN_STYLE = ("synthetic:ats", "A job posting on an applicant tracking system page: About the role, "
            "Responsibilities, Qualifications")

# 某个字段没给时，正文里不许出现的词（出现了说明模型自己编了信息）。re: 开头的是正则
FORBIDDEN_ZH = {
    "salary": ["元/天", "元/月", "薪资", "日薪", "月薪", "薪酬", r"re:\d+\s*[kK]\b"],
    "days_per_week": [r"re:每周.{0,4}\d\s*天", "天/周"],
    "duration_months": [r"re:\d+\s*个月", "实习时长", "实习周期"],
    "can_convert": ["转正", "留用"],
    "deadline": ["截止"],
    "posted_at": ["发布于", "发布时间"],
    "start": ["入职", "到岗时间", "尽快到岗", r"re:\d+\s*月到岗"],
}
FORBIDDEN_EN = {
    "salary": ["usd", "salary", "compensation", "stipend", "per month", "$"],
    "days_per_week": ["per week", "days a week"],
    "duration_months": [r"re:\d+\s*-?\s*months?\b"],
    "can_convert": ["return offer", "full-time offer", "conversion"],
    "deadline": ["apply by", "deadline"],
    "posted_at": ["posted on", "date posted"],
    "start": ["start date", "start in", "starting in"],
}
FIELD_NAMES_ZH = {"salary": "薪资", "days_per_week": "每周到岗天数", "duration_months": "实习时长",
                  "can_convert": "能否转正", "deadline": "投递截止日", "posted_at": "发布日期",
                  "start": "入职时间"}
FIELD_NAMES_EN = {"salary": "salary", "days_per_week": "days per week", "duration_months": "internship length",
                  "can_convert": "return offers", "deadline": "application deadline",
                  "posted_at": "posting date", "start": "start date"}
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]


class SynthJD(BaseModel):
    jd_text: str


@dataclass
class Spec:
    id: str
    company: str
    is_big: bool
    lang: str
    style: str
    title: str
    direction: str | None
    focus: str
    city: str
    job_type: str = "intern"
    salary: tuple[int, int] | None = None
    days_per_week: int | None = None
    duration_months: int | None = None
    can_convert: bool | None = None
    deadline: date | None = None
    posted_at: date | None = None
    start: str | None = None  # "asap" 或 "YYYY-MM"
    is_cold: bool = False
    is_negative: bool = False
    dup_of: str | None = None
    conflict_field: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- 事实短语：必须原样出现在正文里 ----
    def phrases(self) -> dict[str, str]:
        p: dict[str, str] = {"company": self.company, "title": self.title, "city": self.city}
        zh = self.lang == "zh"
        if self.salary:
            lo, hi = self.salary
            p["salary"] = f"{lo}-{hi}元/天" if zh else f"USD {lo}-{hi} per month"
        if self.days_per_week:
            p["days_per_week"] = f"每周至少{self.days_per_week}天" if zh else f"{self.days_per_week} days per week"
        if self.duration_months:
            m = self.duration_months
            p["duration_months"] = f"实习{m}个月以上" if zh else f"{m}-month internship"
        if self.can_convert is not None:
            if zh:
                p["can_convert"] = "表现优秀可转正" if self.can_convert else "本岗位不提供转正机会"
            else:
                p["can_convert"] = ("Strong performers may receive a return offer" if self.can_convert
                                    else "This internship does not include a return offer")
        if self.deadline:
            d = self.deadline
            p["deadline"] = f"投递截止{d.year}年{d.month}月{d.day}日" if zh else f"Apply by {d.isoformat()}"
        if self.posted_at:
            d = self.posted_at
            p["posted_at"] = f"发布于{d.year}年{d.month}月{d.day}日" if zh else f"Posted on {d.isoformat()}"
        if self.start:
            if self.start == "asap":
                p["start"] = "尽快入职" if zh else "Start date: as soon as possible"
            else:
                y, m = (int(x) for x in self.start.split("-"))
                p["start"] = f"{y}年{m}月入职" if zh else f"Start date: {EN_MONTHS[m - 1]} {y}"
        return p

    def missing(self) -> list[str]:
        given = self.phrases()
        return [k for k in FORBIDDEN_ZH if k not in given]

    def truth(self) -> JobFacts:
        p = self.phrases()
        unit = None
        if self.salary:
            unit = "day" if self.lang == "zh" else "month"
        return JobFacts(
            company=self.company, title=self.title,
            directions=[self.direction] if self.direction else [],
            city=self.city, salary_text=p.get("salary"),
            salary_min=self.salary[0] if self.salary else None,
            salary_max=self.salary[1] if self.salary else None, salary_unit=unit,
            job_type=self.job_type, can_convert=self.can_convert, posted_at=self.posted_at,
            deadline=self.deadline, start_text=p.get("start"), duration_months=self.duration_months,
            days_per_week=self.days_per_week)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _hit(word: str, text: str) -> bool:
    if word.startswith("re:"):
        return re.search(word[3:], text, re.I) is not None
    return word.lower() in text.lower()


def check_text(spec: Spec, text: str) -> list[str]:
    """核对正文：给定的事实短语都在（忽略空格），没给的信息没出现。返回问题列表，空表示通过。"""
    flat = _squash(text)
    problems = [f"缺少「{v}」" for v in spec.phrases().values() if _squash(v) not in flat]
    forbidden = FORBIDDEN_ZH if spec.lang == "zh" else FORBIDDEN_EN
    for key in spec.missing():
        hits = [w for w in forbidden[key] if _hit(w, text)]
        if hits:
            problems.append(f"不该写{FIELD_NAMES_ZH[key]}，却写了「{hits[0].removeprefix('re:')}」")
    return problems


def _maybe(rng: random.Random, value: Any, p_missing: float = 0.3) -> Any:
    return None if rng.random() < p_missing else value


def _fill_facts(rng: random.Random, spec: Spec) -> None:
    if spec.lang == "zh":
        lo = rng.choice([150, 200, 250, 300, 350, 400, 500])
        spec.salary = _maybe(rng, (lo, lo + rng.choice([50, 100, 150, 200])))
    else:
        lo = rng.choice([2500, 3000, 3500, 4000])
        spec.salary = _maybe(rng, (lo, lo + rng.choice([500, 1000])))
    spec.days_per_week = _maybe(rng, rng.choice([3, 4, 5]))
    spec.duration_months = _maybe(rng, rng.choice([3, 4, 6]))
    spec.can_convert = _maybe(rng, rng.random() < 0.7)
    spec.deadline = _maybe(rng, date(2026, 10, 25) + timedelta(days=rng.randint(0, 67)))
    spec.posted_at = _maybe(rng, date(2026, 9, 20) + timedelta(days=rng.randint(0, 19)))
    spec.start = _maybe(rng, rng.choice(["asap", "asap", "2026-11", "2026-12"]))


def build_specs(n_normal: int = 30, n_english: int = 4, n_dups: int = 10, seed: int = SEED) -> list[Spec]:
    """30 条普通 + 4 条英文 + 10 条冷门 + 6 条无关 = 50 条底稿，再挑 10 条做转载版本，共 60 条。"""
    rng = random.Random(seed)
    zh_companies = [c for c in COMPANIES if c[2] == "zh"]
    en_companies = [c for c in COMPANIES if c[2] == "en"]
    zh_styles = list(STYLES)
    specs: list[Spec] = []

    def new_id() -> str:
        return f"S{len(specs) + 1:03d}"

    directions = list(DIRECTIONS)
    for i in range(n_normal):
        name, big, _ = zh_companies[i % len(zh_companies)]
        direction = directions[i % len(directions)]
        titles, focus = DIRECTIONS[direction]
        spec = Spec(id=new_id(), company=name, is_big=big, lang="zh", style=rng.choice(zh_styles),
                    title=rng.choice(titles), direction=direction, focus=focus, city=rng.choice(CITIES))
        _fill_facts(rng, spec)
        specs.append(spec)
    en_dirs = list(EN_DIRECTIONS)
    for i in range(n_english):
        name, big, _ = en_companies[i % len(en_companies)]
        direction = en_dirs[i % len(en_dirs)]
        title, focus = EN_DIRECTIONS[direction]
        spec = Spec(id=new_id(), company=name, is_big=big, lang="en", style="ats", title=title,
                    direction=direction, focus=focus, city=rng.choice(EN_CITIES))
        _fill_facts(rng, spec)
        specs.append(spec)
    for i, (title, direction, focus) in enumerate(COLD):
        name, big, _ = zh_companies[(i * 5 + 3) % len(zh_companies)]
        spec = Spec(id=new_id(), company=name, is_big=big, lang="zh", style=rng.choice(zh_styles),
                    title=title, direction=direction, focus=focus, city=rng.choice(CITIES), is_cold=True)
        _fill_facts(rng, spec)
        specs.append(spec)
    for i, (title, focus, job_type) in enumerate(NEGATIVE):
        name, big, _ = zh_companies[(i * 7 + 1) % len(zh_companies)]
        spec = Spec(id=new_id(), company=name, is_big=big, lang="zh", style=rng.choice(zh_styles),
                    title=title, direction=None, focus=focus, city=rng.choice(CITIES), job_type=job_type,
                    is_negative=True)
        _fill_facts(rng, spec)
        specs.append(spec)

    # 转载版本：同一岗位换一个来源再发一次，有一个字段和原版矛盾
    candidates = [s for s in specs if s.lang == "zh" and not s.is_negative
                  and (s.salary or s.deadline or s.days_per_week)]
    for base in rng.sample(candidates, n_dups):
        dup = Spec(**{**base.__dict__, "extra": {}})
        dup.id = new_id()
        dup.dup_of = base.id
        dup.style = rng.choice([s for s in zh_styles if s != base.style])
        options = [f for f in ("salary", "deadline", "days_per_week") if getattr(base, f)]
        dup.conflict_field = rng.choice(options)
        if dup.conflict_field == "salary":
            lo, hi = base.salary
            dup.salary = (lo + 50, hi + 50)
        elif dup.conflict_field == "deadline":
            dup.deadline = base.deadline + timedelta(days=7)
        else:
            dup.days_per_week = 3 if base.days_per_week != 3 else 4
        specs.append(dup)
    return specs


def build_prompt(spec: Spec) -> str:
    phrases = "\n".join(f"- {v}" for k, v in spec.phrases().items() if k not in ("company", "title"))
    missing = spec.missing()
    if spec.lang == "zh":
        style = STYLES[spec.style][1]
        banned = "、".join(FIELD_NAMES_ZH[k] for k in missing) or "（无）"
        return (
            "你在为一个求职系统生成【虚构的】测试数据，请写一条招聘文本。\n"
            f"风格：{style}\n公司：{spec.company}（虚构公司）\n"
            f"岗位名称：{spec.title}（必须原样出现）\n岗位要做的事：{spec.focus}\n"
            f"下面这些短语必须一字不差地出现在正文里，可以嵌在句子中：\n{phrases}\n"
            f"下面这些信息一律不要写，也不要写任何类似的内容：{banned}\n"
            "可以自己补充岗位职责和任职要求。长度 150 到 400 字。"
            "不要出现真实的公司名、人名、网址、电话和邮箱，正文里也不要写“虚构”“测试数据”这类说明。"
        )
    banned = ", ".join(FIELD_NAMES_EN[k] for k in missing) or "(none)"
    return (
        "You are generating FICTIONAL test data for a job-search system. Write one job posting in English.\n"
        f"Style: {EN_STYLE[1]}\nCompany: {spec.company} (fictional)\n"
        f"Job title: {spec.title} (must appear verbatim)\nWhat the role does: {spec.focus}\n"
        f"Each of these phrases must appear verbatim in the text:\n{phrases}\n"
        f"Do NOT mention any of the following, not even vaguely: {banned}\n"
        "You may add responsibilities and qualifications. 120 to 300 words. "
        "No real company names, people, URLs, phone numbers or emails, and do not say the text is fictional."
    )


def generate_one(llm: LLM, spec: Spec, run_id: int | None, max_tries: int = 3) -> dict[str, Any]:
    prompt = build_prompt(spec)
    problems: list[str] = []
    text = ""
    tries = 0
    for tries in range(1, max_tries + 1):
        result = llm.extract(prompt if tries == 1 else
                             f"{prompt}\n\n上一版有这些问题，请改正：{'；'.join(problems)}",
                             SynthJD, purpose="synth_jd", role="chat", run_id=run_id,
                             prompt_version=PROMPT_VERSION, system="你是招聘文案写手。")
        if not result.ok or not result.value:
            problems = [f"输出不合格：{result.error}"]
            continue
        text = result.value.jd_text.strip()
        problems = check_text(spec, text)
        if not problems:
            break
    source, _ = STYLES[spec.style] if spec.lang == "zh" else EN_STYLE
    host = "jobs.example.com" if spec.style in ("official", "ats") else "posts.example.com"
    return {
        "id": spec.id, "source": source, "url": f"https://{host}/{spec.id.lower()}",
        "fetched_at": FETCHED_AT, "lang": spec.lang, "style": spec.style, "raw_text": text,
        "truth": spec.truth().model_dump(mode="json"),
        "meta": {"company_is_big": spec.is_big, "direction": spec.direction, "is_cold": spec.is_cold,
                 "is_negative": spec.is_negative, "dup_of": spec.dup_of, "conflict_field": spec.conflict_field,
                 "checked": not problems, "problems": problems, "tries": tries,
                 "prompt_version": PROMPT_VERSION},
    }


def generate(llm: LLM, specs: list[Spec], run_id: int | None, concurrency: int) -> list[dict[str, Any]]:
    results = parallel_map(lambda s: generate_one(llm, s, run_id), specs, concurrency)
    out = []
    for spec, r in zip(specs, results, strict=True):
        if isinstance(r, Exception):
            raise RuntimeError(f"{spec.id} 生成失败：{r}") from r
        out.append(r)
    return out


def write_fixture(records: list[dict[str, Any]], path: Path = FIXTURE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_fixture(path: Path = FIXTURE) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
