"""录制回放。

真实网页每天都在变，直接拿来测结果没法复现。所以把一次运行里每个工具的返回（和模型的回答）录下来，
之后可以：
- 环境回放：工具从录像里取，模型现场调用。同一个"环境"下比较不同模型、不同提示词。
- 完整回放：工具和模型都从录像里取。不花钱、结果完全一致，用来做回归测试。

录像是一个 JSONL 文件，每行一条：{"kind": "tool" | "llm", ...}。
"""
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Literal

from polaris.tools.base import canonical_args

Mode = Literal["off", "record", "replay"]


class CassetteMiss(LookupError):
    pass


class Cassette:
    def __init__(self, path: Path | str, mode: Mode = "record", llm_replay: bool = False):
        self.path = Path(path)
        self.mode = mode
        self.llm_replay = llm_replay and mode == "replay"
        self.entries: list[dict[str, Any]] = []
        self.meta: dict[str, Any] = {}
        self._tools: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._llm: deque[dict[str, Any]] = deque()
        if mode == "replay":
            self.load()

    @staticmethod
    def key(tool: str, args: dict[str, Any]) -> str:
        return f"{tool}:{canonical_args(args)}"

    def load(self) -> None:
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)
                if e["kind"] == "meta":
                    self.meta = e["meta"]
                elif e["kind"] == "tool":
                    self._tools[e["key"]].append(e)
                elif e["kind"] == "llm":
                    self._llm.append(e)

    def record_meta(self, meta: dict[str, Any]) -> None:
        """记下这次运行的问题、注入的故障等，回放时据此重跑。"""
        if self.mode == "record":
            self.meta = meta
            self.entries.insert(0, {"kind": "meta", "meta": meta})

    def final_answer(self) -> str | None:
        llm = [e for e in self.entries if e["kind"] == "llm"] or list(self._llm)
        return (llm[-1]["message"].get("content") or "") if llm else None

    # ---- 工具 ----
    def record_tool(self, tool: str, args: dict[str, Any], output: str, ok: bool, error_kind: str | None) -> None:
        if self.mode == "record":
            self.entries.append({"kind": "tool", "key": self.key(tool, args), "tool": tool, "args": args,
                                 "output": output, "ok": ok, "error_kind": error_kind})

    def replay_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """同样的工具、同样的参数，按录制时的顺序依次返回；录像里没有就报错（不偷偷去调真的）。"""
        queue = self._tools.get(self.key(tool, args))
        if not queue:
            raise CassetteMiss(f"录像里没有这次调用：{tool} {canonical_args(args)}")
        entry = queue.popleft() if len(queue) > 1 else queue[0]
        return entry

    # ---- 模型 ----
    def record_llm(self, message: dict[str, Any]) -> None:
        if self.mode == "record":
            self.entries.append({"kind": "llm", "message": message})

    def next_llm(self) -> dict[str, Any]:
        if not self._llm:
            raise CassetteMiss("录像里的模型回答已经用完")
        return self._llm.popleft()["message"]

    def save(self) -> None:
        if self.mode != "record":
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
