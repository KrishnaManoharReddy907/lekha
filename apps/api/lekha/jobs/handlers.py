"""Registered background job handlers."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from lekha.jobs.queue import DEAD_HOOKS, handler
from lekha.models import Job


@handler("document.process")
async def document_process(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.pipeline import process_document

    return await process_document(db, uuid.UUID(job.payload["document_id"]))


async def _document_dead(db: AsyncSession, job: Job, error: str) -> None:
    from lekha.services.pipeline import mark_failed

    await mark_failed(db, uuid.UUID(job.payload["document_id"]), error)


DEAD_HOOKS["document.process"] = _document_dead


@handler("client.refresh")
async def client_refresh(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.refresh import refresh_client

    return await refresh_client(db, job.firm_id, uuid.UUID(job.payload["client_id"]))


@handler("reconcile.run")
async def reconcile_run(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.reconcile import run_reconciliation

    return await run_reconciliation(db, job.firm_id, uuid.UUID(job.payload["client_id"]), job.payload["period"],
                                    actor_id=uuid.UUID(job.payload["actor_id"]) if job.payload.get("actor_id") else None)


@handler("close.run")
async def close_run(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.close import execute_close

    return await execute_close(db, job.firm_id, uuid.UUID(job.payload["run_id"]))


@handler("graph.analyse")
async def graph_analyse(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.intelligence import analyse_firm

    return await analyse_firm(db, job.firm_id)


@handler("rules.impact")
async def rules_impact(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.rules_admin import run_impact

    return await run_impact(db, job.firm_id, uuid.UUID(job.payload["report_id"]))


@handler("notice.pack")
async def notice_pack(db: AsyncSession, job: Job) -> dict | None:
    from lekha.services.notices import build_pack

    return await build_pack(db, job.firm_id, uuid.UUID(job.payload["notice_id"]))
