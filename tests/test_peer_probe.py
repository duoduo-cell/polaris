"""问答验收不得把初始化失败或空回答报告为成功，不调用真实模型。"""
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from polaris import config
from polaris.agent import main

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "peer_chat_check.py"


@pytest.mark.parametrize("intro_failed", [True, False])
def test_failed_probe_stops_and_saves_failure_without_live_tools(tmp_path, monkeypatch, intro_failed):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "profile.md").write_text("合成背景", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(database_url="postgresql://test/polaris_live"))
    monkeypatch.setattr(main, "ready", lambda: None)
    monkeypatch.setattr(main, "TOOLS", ("peers_search", "source_open", "platform_search"))
    calls = []

    def chat(message, *args, **kwargs):
        calls.append(message)
        assert main.TOOLS == ("peers_search",)
        return SimpleNamespace(error="连接失败" if intro_failed else None, answer="", tool_calls=[],
                               to_dict=lambda: {"answer": "", "error": None})

    monkeypatch.setattr(main, "chat", chat)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--local-only", "--output", "data/check.json", "问题一", "问题二"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert stopped.value.code == 2
    assert calls == (["合成背景"] if intro_failed else ["合成背景", "问题一"])
    output = json.loads((tmp_path / "data" / "check.json").read_text(encoding="utf-8"))
    assert output["local_only"] and len(output["records"]) == 1
    record = output["records"][0]
    if intro_failed:
        assert record["stage"] == "profile" and record["error"] == "连接失败"
    else:
        assert record["question"] == "问题一" and record["turn"]["answer"] == ""
