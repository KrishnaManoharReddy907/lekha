"""Loads the rule book from the database (published rows), falling back to the built-in seed."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.engines.rules import SEED, RuleBook, RuleVersion
from lekha.models import RuleVersionRow


def row_to_version(r: RuleVersionRow) -> RuleVersion:
    return RuleVersion(r.code, r.version, r.effective_from, r.effective_to, r.params, r.title, r.source, r.status)


async def rulebook_for(db: AsyncSession, firm_id: uuid.UUID | None, include_drafts: list[uuid.UUID] | None = None) -> RuleBook:
    rows = list(
        (
            await db.execute(
                select(RuleVersionRow).where(
                    RuleVersionRow.status == "published",
                    or_(RuleVersionRow.firm_id.is_(None), RuleVersionRow.firm_id == firm_id),
                )
            )
        ).scalars()
    )
    versions = [row_to_version(r) for r in rows] if rows else list(SEED)
    book = RuleBook(versions)
    if include_drafts:
        drafts = (await db.execute(select(RuleVersionRow).where(RuleVersionRow.id.in_(include_drafts)))).scalars()
        book = book.with_overlay([row_to_version(d) for d in drafts])
    return book


async def seed_rules(db: AsyncSession) -> int:
    existing = set((await db.execute(select(RuleVersionRow.code, RuleVersionRow.version))).all())
    n = 0
    for v in SEED:
        if (v.code, v.version) in existing:
            continue
        db.add(RuleVersionRow(code=v.code, version=v.version, title=v.title, source=v.source,
                              effective_from=v.effective_from, effective_to=v.effective_to, params=v.params,
                              status="published"))
        n += 1
    return n
