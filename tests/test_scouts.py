"""侦察子 agent：各来源并行、一个来源出错不影响别的、新查询词的硬性校验。不联网、不调模型。"""
import threading
import time

from polaris.agent import scouts


def test_scouts_run_in_parallel_and_isolate_failures(monkeypatch):
    threads: set[str] = set()

    def slow(source):
        def round_(params):
            threads.add(threading.current_thread().name)
            time.sleep(0.3)
            return [{"run_id": None, "new_ids": [1, 2] if source == "tencent" else [], "big_company_items": 2,
                     "failures": 0, "rate_limited": 0, "searched_queries": ["Agent 实习"]}]
        return round_

    def broken(params):
        raise RuntimeError("登录过期")

    monkeypatch.setattr(scouts, "ROUNDS", {"tencent": slow("tencent"), "sites": slow("sites"), "boss": broken})
    monkeypatch.setattr(scouts, "run_extract", lambda kind, limit, run_id, conc, src: {"extracted": 1, "failed": 0})
    start = time.perf_counter()
    reports = scouts.run_scouts(["tencent", "sites", "boss"])
    assert time.perf_counter() - start < 0.55  # 两个 0.3 秒的侦察是并行跑的
    assert [r["source"] for r in reports] == ["tencent", "sites", "boss"]
    assert reports[0]["new"] == 2 and reports[0]["new_ids"] == ["R-1", "R-2"]
    assert reports[2]["ok"] is False and "登录过期" in reports[2]["error"]
    assert len(threads) >= 2


def test_valid_query():
    known = {"字节 Agent 实习", "Agent 面经"}
    assert scouts.valid_query("美团 大模型 日常实习", known)
    assert scouts.valid_query("Seed Agent 实习", known)
    assert not scouts.valid_query("字节agent实习", known)  # 只差空格和大小写，算重复
    assert not scouts.valid_query("今天吃什么", known)  # 没有大厂也没有方向
    assert not scouts.valid_query("字节跳动豆包大模型团队 Agent 算法日常实习生内推", known)  # 太长
