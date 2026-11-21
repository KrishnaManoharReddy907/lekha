"""Command line: `lekha migrate | worker | seed-rules | demo | bench`."""

from __future__ import annotations

import argparse
import asyncio
import sys


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    from lekha.core.config import API_ROOT

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(cfg, "head")


async def _seed_rules() -> None:
    from lekha.core.db import system_session
    from lekha.services.rulebook import seed_rules

    async with system_session() as db:
        n = await seed_rules(db)
    print(f"Seeded {n} rule versions")


async def _worker() -> None:
    from lekha.core.config import get_settings
    from lekha.jobs.queue import Worker

    w = Worker(concurrency=get_settings().worker_concurrency)
    w.start()
    print("Worker running. Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await w.stop()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lekha")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("seed-rules")
    sub.add_parser("worker")
    demo = sub.add_parser("demo", help="Create the demo workspace")
    demo.add_argument("--email", default="demo@lekha.app")
    bench = sub.add_parser("bench", help="Generate the synthetic benchmark")
    bench.add_argument("--out", default="bench-data")
    bench.add_argument("--n", type=int, default=200)
    bench.add_argument("--seed", type=int, default=7)
    ev = sub.add_parser("bench-eval", help="Run extraction on the benchmark and score it")
    ev.add_argument("--data", default="bench-data")
    ev.add_argument("--limit", type=int, default=50)
    ev.add_argument("--offline", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "migrate":
        _migrate()
        asyncio.run(_seed_rules())
    elif a.cmd == "seed-rules":
        asyncio.run(_seed_rules())
    elif a.cmd == "worker":
        asyncio.run(_worker())
    elif a.cmd == "demo":
        from lekha.services.demo import create_demo_cli

        asyncio.run(create_demo_cli(a.email))
    elif a.cmd == "bench":
        from lekha.bench.generator import write_benchmark

        n = write_benchmark(a.out, a.n, a.seed)
        print(f"Wrote {n} documents to {a.out}")
    elif a.cmd == "bench-eval":
        from lekha.bench.evaluate import run_cli

        asyncio.run(run_cli(a.data, a.limit, a.offline))
    else:  # pragma: no cover
        p.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
