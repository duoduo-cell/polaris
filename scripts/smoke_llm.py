#!/usr/bin/env python3
"""北极星：模型接口冒烟测试。

只用 Python 标准库，不用装任何包。
用法：在项目根目录运行

    python scripts/smoke_llm.py

读取项目根目录 .env 里的 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。
向量服务单独配了 EMBED_BASE_URL、EMBED_API_KEY、EMBED_MODEL、EMBED_DIM 时用它测向量，
没配就用对话服务加 LLM_EMBED_MODEL。环境变量优先于 .env，可以临时换模型测。
结果打印在屏幕上，同时写到 scripts/smoke_result.json（不含 key）。

换成别的服务（比如 Nebius Token Factory）时，只改 .env 里的三行，脚本不用动。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
RESULT_PATH = Path(__file__).resolve().parent / "smoke_result.json"
TIMEOUT = 120

JD_TEXT = (
    "【星河智能】大模型应用开发实习生（Agent 方向）\n"
    "工作地点：北京·海淀\n"
    "岗位职责：参与 Agent 评测体系和垂直领域数据集建设，负责工具调用链路的开发与效果分析。\n"
    "任职要求：熟悉 Python，了解 RAG 和 Agent 框架；每周到岗至少4天，实习期不少于6个月，可尽快入职者优先。\n"
    "薪资：300-400元/天\n"
    "表现优秀者有转正机会。\n"
    "投递截止：2026年11月15日"
)

JOB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "company": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "direction": {"type": ["string", "null"]},
        "city": {"type": ["string", "null"]},
        "days_per_week": {"type": ["integer", "null"]},
        "duration_months": {"type": ["integer", "null"]},
        "salary": {"type": ["string", "null"]},
        "can_convert": {"type": ["boolean", "null"]},
        "deadline": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
    },
    "required": [
        "company", "title", "direction", "city", "days_per_week",
        "duration_months", "salary", "can_convert", "deadline",
    ],
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_jobs",
            "description": "按关键词和城市搜索实习岗位",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "city": {"type": "string"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_job_detail",
            "description": "按岗位编号取岗位详情",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
]


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def call(method, url, key, body=None, stream=False):
    """返回 (状态码, 文本或行列表, 总耗时毫秒, 首包毫秒)。网络错误时状态码为 0。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 polaris-smoke/0.1")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if stream:
                lines, first = [], None
                for raw in resp:
                    if first is None:
                        first = int((time.time() - start) * 1000)
                    lines.append(raw.decode("utf-8", "replace").strip())
                return resp.status, lines, int((time.time() - start) * 1000), first
            text = resp.read().decode("utf-8", "replace")
            return resp.status, text, int((time.time() - start) * 1000), None
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8", "replace")
        return err.code, text, int((time.time() - start) * 1000), None
    except Exception as err:  # 超时、DNS、TLS、连接被拒
        return 0, f"{type(err).__name__}: {err}", int((time.time() - start) * 1000), None


def short(text, limit=300):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def as_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def result(name, ok, ms, detail, **extra):
    row = {"name": name, "ok": bool(ok), "ms": ms, "detail": detail}
    row.update(extra)
    mark = "通过" if ok else "失败"
    print(f"[{mark}] {name:<22} {ms!s:>6} ms  {short(detail, 160)}")
    return row


def find_prefix(base, key):
    """有的服务接口在 /v1 下，有的直接在根下，两种都试。"""
    for prefix in ("/v1", ""):
        status, text, ms, _ = call("GET", base + prefix + "/models", key)
        data = as_json(text) if status == 200 else None
        if data and isinstance(data.get("data"), list):
            ids = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
            return prefix, ids, ms, None
        last = f"HTTP {status} {short(text, 200)}"
    return None, [], 0, last


def chat(base, prefix, key, payload):
    return call("POST", base + prefix + "/chat/completions", key, payload)


def message_of(text):
    data = as_json(text)
    try:
        return data["choices"][0]["message"], data.get("usage")
    except Exception:
        return None, None


def test_chat(base, prefix, key, model, name="chat 基础对话"):
    status, text, ms, _ = chat(base, prefix, key, {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
    })
    msg, usage = message_of(text)
    if status == 200 and msg and msg.get("content"):
        return result(name, True, ms, "模型 {} 回复：{}".format(model, short(msg["content"], 60)), usage=usage)
    return result(name, False, ms, f"HTTP {status} {short(text)}")


def test_responses(base, prefix, key, model):
    status, text, ms, _ = call("POST", base + prefix + "/responses", key, {
        "model": model, "input": "只回复两个字：收到",
    })
    data = as_json(text)
    if status == 200 and isinstance(data, dict) and data.get("output") is not None:
        out = ""
        for item in data.get("output") or []:
            for part in item.get("content") or []:
                out += part.get("text") or ""
        return result("responses 接口", True, ms, "回复：{}".format(short(out or "(无文本)", 60)), usage=data.get("usage"))
    return result("responses 接口", False, ms, f"HTTP {status} {short(text)}")


def test_stream(base, prefix, key, model):
    status, lines, ms, first = call("POST", base + prefix + "/chat/completions", key, {
        "model": model, "stream": True,
        "messages": [{"role": "user", "content": "用一句话介绍哈尔滨。"}],
    }, stream=True)
    if status == 200 and isinstance(lines, list):
        chunks = [line for line in lines if line.startswith("data:") and "[DONE]" not in line]
        ok = len(chunks) > 1
        return result("流式输出", ok, ms, f"{len(chunks)} 个数据块，首包 {first} ms", first_ms=first)
    return result("流式输出", False, ms, f"HTTP {status} {short(lines)}")


def test_tool_single(base, prefix, key, model, rounds=3):
    hits, total_ms, notes = 0, 0, []
    for _ in range(rounds):
        status, text, ms, _ = chat(base, prefix, key, {
            "model": model, "tools": TOOLS, "tool_choice": "auto",
            "messages": [{"role": "user", "content": "帮我查一下岗位 J-1024 的详情。"}],
        })
        total_ms += ms
        msg, _ = message_of(text)
        calls = (msg or {}).get("tool_calls") or []
        good = False
        if calls:
            fn = calls[0].get("function", {})
            args = as_json(fn.get("arguments") or "") or {}
            good = fn.get("name") == "get_job_detail" and "1024" in str(args.get("job_id", ""))
        hits += 1 if good else 0
        if not good:
            notes.append(f"HTTP {status} {short(text, 150)}")
    detail = f"{hits}/{rounds} 次选对工具并填对参数"
    if notes:
        detail += "；失败样例：" + notes[0]
    return result("工具调用（单步）", hits == rounds, total_ms // rounds, detail, hits=hits, rounds=rounds)


def test_tool_roundtrip(base, prefix, key, model):
    messages = [{"role": "user", "content": "搜一下北京的 Agent 实习，然后告诉我搜到了哪家公司。"}]
    status, text, ms1, _ = chat(base, prefix, key, {
        "model": model, "tools": TOOLS, "tool_choice": "auto", "messages": messages,
    })
    msg, _ = message_of(text)
    calls = (msg or {}).get("tool_calls") or []
    if not calls:
        return result("工具调用（往返）", False, ms1, f"第一步没有发起工具调用：HTTP {status} {short(text, 200)}")
    first = calls[0]
    args = as_json(first.get("function", {}).get("arguments") or "") or {}
    messages.append(msg)
    for c in calls:
        messages.append({
            "role": "tool", "tool_call_id": c.get("id"),
            "content": json.dumps({"jobs": [{"job_id": "J-2048", "company": "星河智能", "title": "Agent 开发实习生"}]}, ensure_ascii=False),
        })
    status, text, ms2, _ = chat(base, prefix, key, {"model": model, "tools": TOOLS, "messages": messages})
    msg2, _ = message_of(text)
    answer = (msg2 or {}).get("content") or ""
    ok = first.get("function", {}).get("name") == "search_jobs" and "星河智能" in answer
    detail = "第一步调用 {}{}；最终回答{}工具结果".format(
        first.get("function", {}).get("name"), json.dumps(args, ensure_ascii=False), "用上了" if "星河智能" in answer else "没用上")
    if not ok:
        detail += f"：HTTP {status} {short(text, 150)}"
    return result("工具调用（往返）", ok, ms1 + ms2, detail)


def check_job(job):
    checks = {
        "company": "星河智能" in str(job.get("company")),
        "city": "北京" in str(job.get("city")),
        "days_per_week": job.get("days_per_week") == 4,
        "duration_months": job.get("duration_months") == 6,
        "salary": "300" in str(job.get("salary")),
        "can_convert": job.get("can_convert") is True,
        "deadline": str(job.get("deadline")) == "2026-11-15",
    }
    return checks


def test_schema(base, prefix, key, model):
    prompt = "从下面的岗位描述里抽取字段，抽不到的填 null，截止日用 YYYY-MM-DD。\n\n" + JD_TEXT
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "job", "strict": True, "schema": JOB_SCHEMA}},
    }
    status, text, ms, _ = chat(base, prefix, key, payload)
    mode = "json_schema"
    msg, _ = message_of(text)
    if status != 200 or not msg:
        first_error = f"HTTP {status} {short(text, 150)}"
        payload["response_format"] = {"type": "json_object"}
        payload["messages"][0]["content"] = prompt + "\n\n只输出 JSON，键为：" + "、".join(JOB_SCHEMA["required"])
        status, text, ms, _ = chat(base, prefix, key, payload)
        mode = f"json_object（json_schema 不支持：{first_error}）"
        msg, _ = message_of(text)
    job = as_json((msg or {}).get("content") or "")
    if status != 200 or not isinstance(job, dict):
        return result("按 schema 抽取中文岗位", False, ms, f"HTTP {status} {short(text)}")
    checks = check_job(job)
    right = sum(checks.values())
    wrong = [k for k, v in checks.items() if not v]
    detail = f"{mode}；{right}/{len(checks)} 个字段正确"
    if wrong:
        detail += f"；不对的字段：{'、'.join(wrong)}；原始输出：{short(job, 200)}"
    return result("按 schema 抽取中文岗位", right == len(checks) and mode == "json_schema", ms, detail,
                  fields_right=right, fields_total=len(checks), mode=mode)


def test_embedding(url, key, model, want_dim=None):
    body = {"model": model, "input": ["Agent 开发实习生", "大模型评测实习"]}
    if want_dim:
        body["dimensions"] = want_dim
    status, text, ms, _ = call("POST", url, key, body)
    data = as_json(text)
    try:
        dim = len(data["data"][0]["embedding"])
    except Exception:
        return result("向量接口", False, ms, f"模型 {model}：HTTP {status} {short(text)}")
    ok = not want_dim or dim == want_dim
    detail = f"{urlparse(url).netloc} 上的模型 {model}，{dim} 维"
    if want_dim and not ok:
        detail += f"（要的是 {want_dim} 维）"
    return result("向量接口", ok, ms, detail, dim=dim, usage=data.get("usage"))


def pick(ids, words, exclude=()):
    for mid in ids:
        low = mid.lower()
        if all(w in low for w in words) and not any(x in low for x in exclude):
            return mid
    return None


def key_problem(key):
    """key 明显不对时返回原因，否则返回 None。不打印 key 本身。"""
    bad = [c for c in key if not 33 <= ord(c) <= 126]
    if bad:
        return f"含 {len(bad)} 个不可见字符（Windows 上按 Ctrl+V 粘贴时常见，粘贴没成功，存进去的是按键本身）"
    if len(key) < 8:
        return f"只有 {len(key)} 个字符，太短"
    return None


def ask_config(base, key, model, embed_model):
    """第一次运行时在命令行里问三项配置，并存成项目根目录的 .env。"""
    import getpass
    print("没有找到 .env，下面问三项配置，填完会自动存成 .env（这个文件不进仓库）。\n")
    base = base or input("接口地址（例如 https://example.com）：").strip().rstrip("/")
    model = model or input("模型名（例如 gpt-5.5）：").strip()
    if not key:
        key = getpass.getpass("API key（用鼠标右键粘贴，Ctrl+V 可能无效；粘贴后屏幕上不显示，直接回车）：").strip()
        problem = key_problem(key) if key else None
        if problem:
            print(f"这个 key {problem}。没有保存，请重新运行，或直接用记事本编辑 .env。")
            key = ""
    if base and key and model:
        (ROOT / ".env").write_text(
            "# 模型服务配置。含 key，已在 .gitignore 里，不进仓库。\n"
            "# 换成 Nebius Token Factory 时只改下面几行，写法见 .env.example。\n"
            f"LLM_BASE_URL={base}\nLLM_API_KEY={key}\nLLM_MODEL={model}\nLLM_EMBED_MODEL={embed_model}\n",
            encoding="utf-8")
        print("已保存到 %s\n" % (ROOT / ".env"))
    return base, key, model


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_env(ROOT / ".env")
    base = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
    key = os.environ.get("LLM_API_KEY") or ""
    model = os.environ.get("LLM_MODEL") or ""
    embed_model = os.environ.get("LLM_EMBED_MODEL") or "text-embedding-3-small"
    if not (base and key and model):
        if not sys.stdin.isatty():
            print("缺少配置。请在项目根目录的 .env 里写好 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。")
            return 2
        base, key, model = ask_config(base, key, model, embed_model)
        if not (base and key and model):
            print("三项都要填，已退出。")
            return 2
    problem = key_problem(key)
    if problem:
        print(f".env 里的 LLM_API_KEY {problem}。请用记事本打开 .env，在 LLM_API_KEY= 后面重新粘贴 key。")
        return 2
    if base.endswith("/v1"):
        base = base[:-3]

    print(f"服务：{urlparse(base).netloc}   模型：{model}\n")
    rows = []

    prefix, ids, ms, error = find_prefix(base, key)
    if prefix is None:
        rows.append(result("模型列表", False, ms, error or "拿不到模型列表"))
        prefix = "/v1"  # 列表拿不到也继续试对话接口
    else:
        rows.append(result("模型列表", True, ms, f"接口前缀 {prefix or '/'!r}，共 {len(ids)} 个模型", prefix=prefix))

    rows.append(test_chat(base, prefix, key, model))
    chat_ok = rows[-1]["ok"]
    rows.append(test_responses(base, prefix, key, model))
    if chat_ok:
        rows.append(test_stream(base, prefix, key, model))
        rows.append(test_tool_single(base, prefix, key, model))
        rows.append(test_tool_roundtrip(base, prefix, key, model))
        rows.append(test_schema(base, prefix, key, model))
    else:
        print("chat 接口不通，跳过流式、工具调用和结构化输出。")
    # 向量可以单独配一个服务（EMBED_*），没配就用对话服务
    embed_base = (os.environ.get("EMBED_BASE_URL") or "").rstrip("/")
    embed_key = os.environ.get("EMBED_API_KEY") or ""
    if embed_base and embed_key:
        embed_model = os.environ.get("EMBED_MODEL") or embed_model
        want_dim = int(os.environ.get("EMBED_DIM") or 0) or None
        problem = key_problem(embed_key)
        if problem:
            rows.append(result("向量接口", False, 0, f"EMBED_API_KEY {problem}"))
        else:
            rows.append(test_embedding(embed_base + "/embeddings", embed_key, embed_model, want_dim))
    else:
        rows.append(test_embedding(base + prefix + "/embeddings", key, embed_model))

    # 顺手看看便宜的备选模型能不能用
    others = []
    for words, exclude in ((("deepseek",), ("reasoner", "r1")), (("mini",), ("audio", "realtime", "tts", "image")), (("embedding",), ())):
        mid = pick(ids, words, exclude)
        if mid and mid != model and mid not in others:
            others.append(mid)
    for mid in others:
        if "embedding" in mid.lower():
            if mid != embed_model:
                rows.append(test_embedding(base + prefix + "/embeddings", key, mid))
        else:
            rows.append(test_chat(base, prefix, key, mid, name="备选模型对话"))

    passed = sum(1 for r in rows if r["ok"])
    print(f"\n合计：{passed} 项通过，{len(rows) - passed} 项失败")
    RESULT_PATH.write_text(json.dumps({
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": urlparse(base).netloc,
        "model": model,
        "embed_model": embed_model,
        "models_listed": ids[:200],
        "tests": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写到 {RESULT_PATH}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
