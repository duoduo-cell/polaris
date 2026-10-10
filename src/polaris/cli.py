"""命令行：uv run polaris --help"""
import sys
from collections import Counter
from pathlib import Path

import typer

from polaris.config import ROOT, get_settings
from polaris.ingest import IngestResult, RawItemIn, ingest_raw_items
from polaris.runs import run_scope
from polaris.synth.jobs import FIXTURE, read_fixture

if (sys.stdout.encoding or "").lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="Polaris Core 命令行", no_args_is_help=True, add_completion=False)
metrics_app = typer.Typer(help="各模块的指标，结果写到 eval/reports/", no_args_is_help=True)
app.add_typer(metrics_app, name="metrics")


@app.command("db-upgrade")
def db_upgrade() -> None:
    """把数据库表结构迁移到最新版本。"""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    typer.echo("表结构已是最新。")


@app.command()
def synth(out: Path = typer.Option(FIXTURE, help="输出文件"),
          concurrency: int = typer.Option(0, help="并发数，0 表示用配置里的 LLM_CONCURRENCY"),
          limit: int = typer.Option(0, help="只生成前几条，调试用")) -> None:
    """用模型生成合成岗位数据（会调模型，花钱）。"""
    from polaris.llm import LLM
    from polaris.synth.jobs import PROMPT_VERSION, SEED, build_specs, generate, write_fixture

    specs = build_specs()
    if limit:
        specs = specs[:limit]
    workers = concurrency or get_settings().llm_concurrency
    with run_scope("synth", {"seed": SEED, "n": len(specs), "prompt_version": PROMPT_VERSION}) as (run_id, stats):
        records = generate(LLM(), specs, run_id, workers)
        write_fixture(records, out)
        stats.update({"records": len(records), "checked": sum(r["meta"]["checked"] for r in records),
                      "tries": dict(Counter(r["meta"]["tries"] for r in records))})
    typer.echo(f"生成 {len(records)} 条，核对通过 {stats['checked']} 条，写到 {out}")
    bad = [r["id"] for r in records if not r["meta"]["checked"]]
    if bad:
        typer.echo(f"没通过核对（评测时排除）：{', '.join(bad)}")


def seed_records(path: Path, run_id: int | None) -> IngestResult:
    items = [RawItemIn(source=r["source"], tier="A", kind="job", url=r["url"], external_id=r["id"],
                       raw_text=r["raw_text"], fetched_at=r["fetched_at"], is_synthetic=True,
                       raw_json={"synthetic_id": r["id"], "lang": r["lang"], "style": r["style"]})
             for r in read_fixture(path) if r["raw_text"]]
    return ingest_raw_items(items, run_id)


@app.command()
def seed(file: Path = typer.Option(FIXTURE, help="合成数据文件")) -> None:
    """把合成数据写进数据库。重复运行不会重复写入。"""
    with run_scope("seed", {"file": str(file.relative_to(ROOT) if file.is_relative_to(ROOT) else file)}) as (
            run_id, stats):
        result = seed_records(file, run_id)
        stats.update(result.model_dump(exclude={"ids"}))
    typer.echo(f"读到 {result.received} 条，新写入 {result.inserted} 条，已存在 {result.duplicates} 条。")


@metrics_app.command("m1")
def metrics_m1() -> None:
    """M1：追踪完整率、结构化输出、延迟、合成数据核对、入库幂等、缓存。"""
    from polaris.eval import m1

    stats = m1.run()
    typer.echo((m1.REPORT_DIR / "M1.md").read_text(encoding="utf-8"))
    typer.echo(f"已写到 {m1.REPORT_DIR / 'M1.md'}（run {stats.get('synth', {}).get('run_id', '—')}）")


if __name__ == "__main__":
    app()
