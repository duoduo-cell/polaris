"""最近采集是实际时刻；中文界面不能提前截断 UTC 自然日。"""
from datetime import UTC, date, datetime

import pytest

from polaris.db.models import Job
from polaris.tools.jobs import _job_row


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("instant,expected", [
    (datetime(2026, 10, 22, 15, 59, 59, tzinfo=UTC), "2026-10-22"),
    (datetime(2026, 10, 22, 16, 0, tzinfo=UTC), "2026-10-23"),
    (datetime(2026, 10, 22, 19, 0, tzinfo=UTC), "2026-10-23"),
])
def test_job_seen_day_uses_beijing_in_summary_and_detail(instant, expected, detailed):
    job = Job(id=1, company="合成公司", title="合成岗位", posting_count=1,
              last_seen_at=instant, posted_at=date(2026, 10, 22))
    row = _job_row(job, detailed)
    assert row["last_seen"] == expected
    assert row["posted_at"] == "2026-10-22"  # 来源日历日期不随采集时刻更改。
    assert job.last_seen_at == instant
