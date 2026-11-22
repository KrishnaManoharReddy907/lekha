"""Postgres-backed job queue.

Jobs are rows in `jobs`. Workers claim one with FOR UPDATE SKIP LOCKED, so many workers can
run safely in parallel. Enqueueing happens in the same transaction as the change that caused
it, so a job never runs for data that was rolled back. Failures retry with exponential
backoff, then move to `dead` for inspection.
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.db import system_session, tenant_session
from lekha.core.memory import release_memory
from lekha.models import Job

log = structlog.get_logger("jobs")
Handler = Callable[[AsyncSession, Job], Awaitable[dict | None]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    firm_id: uuid.UUID | None,
    delay_s: float = 0,
    dedupe_key: str | None = None,
    max_attempts: int = 4,
) -> Job:
    if dedupe_key:
        existing = (
            await db.execute(
                select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(("queued", "running")))
            )
        ).scalar_one_or_none()
        if existing:
            return existing
    job = Job(
        firm_id=firm_id,
        kind=kind,
        payload=payload or {},
        run_after=datetime.now(UTC) + timedelta(seconds=delay_s),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    db.add(job)
    await db.flush()
    return job


async def claim_one(include_delayed: bool = False) -> Job | None:
    due = "" if include_delayed else "AND run_after <= now()"
    async with system_session() as db:
        row = (
            await db.execute(
                text(
                    f"""
                    UPDATE jobs SET status='running', started_at=now(), attempts=attempts+1, updated_at=now()
                    WHERE id = (
                      SELECT id FROM jobs WHERE status='queued' {due}
                      ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1
                    ) RETURNING id
                    """
                )
            )
        ).first()
        if not row:
            return None
        job = await db.get(Job, row[0])
        if job:
            db.expunge(job)
        return job


async def run_job(job: Job) -> None:
    fn = HANDLERS.get(job.kind)
    started = datetime.now(UTC)
    try:
        if fn is None:
            raise RuntimeError(f"No handler for job kind {job.kind}")
        async with tenant_session(job.firm_id, bypass=job.firm_id is None) as db:
            result = await fn(db, job)
        async with system_session() as db:
            await db.execute(
                text("UPDATE jobs SET status='done', finished_at=now(), result=CAST(:r AS jsonb), last_error=NULL "
                     "WHERE id=:id"),
                {"id": job.id, "r": _json(result or {})},
            )
        log.info("job.done", kind=job.kind, id=str(job.id), ms=int((datetime.now(UTC) - started).total_seconds() * 1000))
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}"
        dead = job.attempts >= job.max_attempts
        backoff = min(600, 5 * 2 ** (job.attempts - 1))
        async with system_session() as db:
            await db.execute(
                text(
                    "UPDATE jobs SET status=:s, last_error=:e, finished_at=now(), "
                    "run_after=now() + make_interval(secs => :b) WHERE id=:id"
                ),
                {"s": "dead" if dead else "queued", "e": err[:8000], "b": backoff, "id": job.id},
            )
        log.warning("job.failed", kind=job.kind, id=str(job.id), attempt=job.attempts, dead=dead, error=str(exc))
        if dead:
            await _on_dead(job, str(exc))
    finally:
        release_memory()


async def _on_dead(job: Job, error: str) -> None:
    hook = DEAD_HOOKS.get(job.kind)
    if hook:
        try:
            async with tenant_session(job.firm_id, bypass=job.firm_id is None) as db:
                await hook(db, job, error)
        except Exception:
            log.exception("job.dead_hook_failed", kind=job.kind)


DEAD_HOOKS: dict[str, Callable[[AsyncSession, Job, str], Awaitable[None]]] = {}


async def run_until_empty(max_jobs: int = 1000, include_delayed: bool = True) -> int:
    """Process jobs synchronously until none are left (tests, CLI, demo seeding). Delayed jobs run immediately."""
    n = 0
    while n < max_jobs:
        job = await claim_one(include_delayed)
        if not job:
            break
        await run_job(job)
        n += 1
    return n


class Worker:
    def __init__(self, concurrency: int = 2, idle_sleep: float = 1.0):
        self.concurrency = concurrency
        self.idle_sleep = idle_sleep
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def _loop(self, n: int) -> None:
        while not self._stop.is_set():
            try:
                job = await claim_one()
            except Exception:
                log.exception("worker.claim_failed")
                await asyncio.sleep(3)
                continue
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.idle_sleep)
                except TimeoutError:
                    pass
                continue
            await run_job(job)

    def start(self) -> None:
        import lekha.jobs.handlers  # noqa: F401 - register handlers

        self._tasks = [asyncio.create_task(self._loop(i)) for i in range(self.concurrency)]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def _json(v: Any) -> str:
    import json

    from lekha.core.audit import jsonable

    return json.dumps(jsonable(v))
