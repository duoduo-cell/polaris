"""M3 指标：近似重复识别；工具套件里查过小红书笔记的录像和回答只留在本机。"""
import json

from polaris.eval import m3


def test_near_duplicates_catch_reposted_notes():
    a = "米哈游目前在紧急补招实习生，基础日薪260/天，接受无经验，可预订寒假！每周线下到岗至少4天，实习时长最低3个月"
    b = "米哈游目前在紧急补招实习生，基础日薪260/天，接受无经验，可预订寒假！！！每周线下到岗至少4天，实习时长最低3个月 #找实习[话题]#"
    c = "滴滴后端一面，主要拷打项目，四十分钟，无手撕。知识库怎么存储、建设、分层，RAG 的流程"
    assert m3.near_duplicates({1: a, 2: b, 3: c}) == [(1, 2)]


def test_tool_suite_keeps_post_content_local(tmp_path, monkeypatch):
    pub, priv = tmp_path / "eval", tmp_path / "data"
    monkeypatch.setattr(m3, "PUBLIC_CASSETTES", pub)
    monkeypatch.setattr(m3, "PRIVATE_CASSETTES", priv)
    monkeypatch.setattr(m3, "PRIVATE_REPORT", tmp_path / "full.json")
    monkeypatch.setattr(m3, "TOOLS_JSON", tmp_path / "M3-tools.json")

    def fake_run_suite(tasks, **kw):
        (pub / "jobs-task.jsonl").write_text('{"kind": "tool", "tool": "jobs_search"}\n', encoding="utf-8")
        (pub / "posts-task.jsonl").write_text('{"kind": "tool", "tool": "posts_search"}\n', encoding="utf-8")
        results = [{"task": "jobs-task", "tools": ["jobs_search"], "answer": "J-1"},
                   {"task": "posts-task", "tools": ["posts_search"], "answer": "笔记原文片段"}]
        return {"results": results, "failures": [{"task": "posts-task", "answer": "笔记原文片段"}]}

    import polaris.eval.runner as runner
    import polaris.eval.suites.real_v1 as real_v1
    monkeypatch.setattr(runner, "run_suite", fake_run_suite)
    monkeypatch.setattr(real_v1, "build_tasks", lambda: [])
    out = m3.run_tools(k=1)
    assert (pub / "jobs-task.jsonl").exists() and not (pub / "posts-task.jsonl").exists()
    assert (priv / "posts-task.jsonl").exists()
    public = (tmp_path / "M3-tools.json").read_text(encoding="utf-8")
    assert "笔记原文片段" not in public and "J-1" in public
    assert "笔记原文片段" in (tmp_path / "full.json").read_text(encoding="utf-8")
    assert json.loads(public)["private_tasks"] == ["posts-task"] == out["private_tasks"]
