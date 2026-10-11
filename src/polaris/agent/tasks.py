"""数据库任务队列。单个会话串行，不同会话并行；网页断开不影响任务。

独占 PostgreSQL 会话锁的调度器负责认领任务。服务意外重启时明确标记中断，
不自动重放可能已生效的工具操作；仍在排队的任务继续执行。
"""
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, case, select, text

from polaris.agent.transcript import ensure_thread, finish_turn, lock_thread, visible_messages
from polaris.db.models import ChatMessage, ChatTask, ChatThread
from polaris.db.session import get_engine, session_scope

log = logging.getLogger(__name__)
ACTIVE = ("queued", "running")
LEADER_LOCK = 739514622


class TaskConflict(ValueError):
    pass


def task_dict(task: ChatTask) -> dict[str, Any]:
    return {"id": task.id, "thread_id": task.thread_id, "kind": task.kind, "state": task.state,
            "question": task.payload.get("message", ""),
            "activities": task.activities, "result": task.result, "error": task.error,
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None}


def submit(thread_id: str, kind: str, payload: dict[str, Any], request_key: str, *, start: bool = True) -> dict[str, Any]:
    with session_scope() as s:
        lock_thread(s, f"request:{request_key}")
        old = s.scalar(select(ChatTask).where(ChatTask.request_key == request_key))
        if old:
            if (old.thread_id, old.kind, old.payload) != (thread_id, kind, payload):
                raise TaskConflict("同一个请求编号不能用于不同的消息")
            if start:
                service().wake.set()
            return task_dict(old)
        thread = ensure_thread(s, thread_id)
        pending = s.scalars(select(ChatTask).where(ChatTask.thread_id == thread_id, ChatTask.state.in_(ACTIVE))).all()
        if kind == "resume":
            if not thread.approvals or any(t.kind == "resume" for t in pending):
                raise TaskConflict("没有待审批动作，或你的决定已经提交")
        elif thread.approvals:
            raise TaskConflict("请先批准或拒绝待审批动作")
        if len(pending) >= 20:
            raise TaskConflict("这段对话已有 20 条消息在处理，请等一会儿再发送")
        task = ChatTask(id=str(uuid4()), request_key=request_key, thread_id=thread_id, kind=kind, payload=payload)
        s.add(task)
        s.flush()
        if kind == "chat":
            s.add(ChatMessage(thread_id=thread_id, source_key=f"{task.id}:user", role="user", content=payload["message"]))
        else:
            for waiting in s.scalars(select(ChatTask).where(ChatTask.thread_id == thread_id, ChatTask.state == "waiting_approval")):
                waiting.state = "continued"
        result = task_dict(task)
    if start:
        service().wake.set()
    return result


def snapshot(thread_id: str) -> dict[str, Any]:
    with session_scope() as s:
        thread = ensure_thread(s, thread_id)
        tasks = s.scalars(select(ChatTask).where(ChatTask.thread_id == thread_id)
                          .order_by(ChatTask.created_at.desc()).limit(50)).all()
        return {"thread_id": thread_id, "messages": visible_messages(s, thread_id),
                "approvals": thread.approvals, "tasks": [task_dict(t) for t in reversed(tasks)]}


def thread_list() -> list[dict[str, Any]]:
    with session_scope() as s:
        threads = s.scalars(select(ChatThread).order_by(ChatThread.created_at.desc()).limit(100)).all()
        out = []
        for thread in threads:
            first = s.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.id, ChatMessage.role == "user")
                             .order_by(ChatMessage.id).limit(1))
            active = s.scalar(select(ChatTask.id).where(ChatTask.thread_id == thread.id, ChatTask.state.in_(ACTIVE)).limit(1))
            if first or active or thread.approvals:
                out.append({"id": thread.id, "title": first.content[:40] if first else "新对话", "active": bool(active),
                            "waiting_approval": bool(thread.approvals)})
        return out


class TaskService:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="polaris-chat-tasks", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        self.thread.join(timeout=10)

    def recover(self) -> None:
        from polaris.agent.main import checkpointer
        from polaris.agent.transcript import checkpoint_approvals

        with session_scope(self.engine) as s:
            for task in s.scalars(select(ChatTask).where(ChatTask.state == "running")):
                lock_thread(s, task.thread_id)
                saved = checkpointer().get_tuple({"configurable": {"thread_id": task.thread_id, "checkpoint_ns": ""}})
                approvals = checkpoint_approvals(saved) if saved else []
                task.state = "waiting_approval" if approvals else "interrupted"
                task.error = None if approvals else "后端重启中断了这次执行，原话已保留。请重新发送需要继续的请求。"
                task.finished_at = datetime.now(UTC)
                s.get(ChatThread, task.thread_id).approvals = approvals

    def _claim(self) -> str | None:
        with session_scope(self.engine) as s:
            queued = s.scalars(select(ChatTask).where(ChatTask.state == "queued")
                               .order_by(case((ChatTask.kind == "resume", 0), else_=1), ChatTask.created_at).limit(100)).all()
            for task in queued:
                lock_thread(s, task.thread_id)
                running = s.scalar(select(ChatTask.id).where(ChatTask.thread_id == task.thread_id, ChatTask.state == "running"))
                if running or (s.get(ChatThread, task.thread_id).approvals and task.kind != "resume"):
                    continue
                task.state, task.started_at = "running", datetime.now(UTC)
                return task.id
        return None

    def _event(self, task_id: str, event: dict[str, Any]) -> None:
        # 模型中间文字可能是工具调用前的套话或私有思考，不存进可见回复或活动。
        if event.get("type") not in ("memory", "tool"):
            return
        with session_scope(self.engine) as s:
            task = s.get(ChatTask, task_id)
            if task and task.state == "running":
                task.activities = [*task.activities, event][-100:]

    def _work(self, task_id: str) -> None:
        from polaris.agent.main import chat, resume

        try:
            with session_scope(self.engine) as s:
                task = s.get(ChatTask, task_id)
                thread_id, kind, payload = task.thread_id, task.kind, task.payload
            event = lambda e: self._event(task_id, e)  # noqa: E731
            turn = (chat(payload["message"], thread_id, on_event=event, record=False) if kind == "chat" else
                    resume(thread_id, payload["approve"], payload.get("reason", ""), on_event=event, record=False))
            result = turn.to_dict()
            if kind == "chat":
                result["question"] = payload["message"]
            else:
                with session_scope(self.engine) as s:
                    parent = s.scalar(select(ChatTask).where(ChatTask.thread_id == thread_id, ChatTask.state == "continued",
                                                            ChatTask.kind == "chat").order_by(ChatTask.created_at.desc()).limit(1))
                    result["question"] = parent.payload["message"] if parent else ""
            with session_scope(self.engine) as s:
                task = s.get(ChatTask, task_id)
                finish_turn(s, thread_id, task_id, result)
                task.result, task.error, task.finished_at = result, turn.error, datetime.now(UTC)
                task.state = "waiting_approval" if turn.approvals else ("failed" if turn.error and not turn.answer else "completed")
        except Exception as e:  # noqa: BLE001  故障也必须持久化，重连才能看见
            log.exception("chat task failed: %s", task_id)
            with session_scope(self.engine) as s:
                task = s.get(ChatTask, task_id)
                task.state, task.error, task.finished_at = "failed", f"{type(e).__name__}: {e}"[:500], datetime.now(UTC)
        finally:
            self.wake.set()

    def _loop(self) -> None:
        with self.engine.connect() as leader:
            while not self.stopping.is_set():
                if leader.scalar(text("select pg_try_advisory_lock(:key)"), {"key": LEADER_LOCK}):
                    leader.commit()
                    break
                leader.commit()
                self.wake.wait(0.5)
                self.wake.clear()
            else:
                return
            try:
                self.recover()
                with ThreadPoolExecutor(max_workers=4, thread_name_prefix="polaris-chat") as pool:
                    futures: list[Future] = []
                    while not self.stopping.is_set():
                        futures = [f for f in futures if not f.done()]
                        while len(futures) < 4 and not self.stopping.is_set():
                            task_id = self._claim()
                            if task_id is None:
                                break
                            futures.append(pool.submit(self._work, task_id))
                        self.wake.wait(0.5)
                        self.wake.clear()
            except Exception:
                log.exception("chat task dispatcher stopped")
            finally:
                leader.execute(text("select pg_advisory_unlock(:key)"), {"key": LEADER_LOCK})
                leader.commit()


_services: dict[Engine, TaskService] = {}
_service_lock = threading.Lock()


def service() -> TaskService:
    engine = get_engine()
    with _service_lock:
        instance = _services.get(engine)
        if instance is None or not instance.thread.is_alive():
            instance = _services[engine] = TaskService(engine)
        return instance


def shutdown() -> None:
    with _service_lock:
        instances = list(_services.values())
        _services.clear()
    for instance in instances:
        instance.stop()
