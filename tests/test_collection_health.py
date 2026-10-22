from types import SimpleNamespace

from sqlalchemy import select

from polaris.collectors import rounds
from polaris.db.models import Run
from polaris.db.session import session_scope


def test_all_searches_failed_is_not_success_or_budget_exhaustion(db, monkeypatch):
    monkeypatch.setattr(rounds.q, "ensure_seeded", lambda _: None)
    monkeypatch.setattr(rounds.q, "pick", lambda *a: ["Agent"])
    monkeypatch.setattr(rounds.q, "mark", lambda *a: (_ for _ in ()).throw(AssertionError("failed query must retry")))

    class Collector:
        stats = SimpleNamespace(searched=0, failures=0, calls=0)

        def collect(self, query):
            self.stats.failures += 1
            self.stats.calls += 1
            yield from ()

    logs = []
    r = rounds.collect_round("maimai", Collector(), 1, {}, echo=logs.append)
    with session_scope() as s:
        run = s.execute(select(Run).where(Run.id == r["run_id"])).scalar_one()
        assert run.status == "failed"
    assert any("搜索失败" in log for log in logs)
    assert not any("预算用完" in log for log in logs)
