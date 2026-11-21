"""Turn ORM rows into JSON-safe dicts. Money stays a string ("118000.00") so no precision is lost."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect

from lekha.core.audit import jsonable


def row(obj: Any, exclude: tuple[str, ...] = (), extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if obj is None:
        return {}
    mapper = sa_inspect(obj).mapper
    out = {c.key: jsonable(getattr(obj, c.key)) for c in mapper.column_attrs if c.key not in exclude}
    if extra:
        out.update(jsonable(extra))
    return out


def rows(objs, exclude: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [row(o, exclude) for o in objs]


def page(items: list[dict[str, Any]], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}
