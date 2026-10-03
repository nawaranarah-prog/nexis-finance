"""Where Nexis Pulse discussions come from.

Every discussion row carries a ``source`` key. Today the only source is the Nexis community itself.
A permitted external source (for example an approved X or Reddit integration, or news) would be added by
writing a class with the same interface, registering it in ``SOURCES``, and storing its items with its own
key — the scoring, topics and pages work on the stored rows and don't change.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Select

from app.models import Post


class DiscussionSource(Protocol):
    key: str
    label: str
    # True when people write the discussions on Nexis; False for imported sources.
    native: bool

    def restrict(self, query: Select) -> Select:
        """Limit a query over posts to this source's discussions."""
        ...


class NexisSource:
    key = "nexis"
    label = "Nexis Community"
    native = True

    def restrict(self, query: Select) -> Select:
        return query.where(Post.source == self.key)


SOURCES: dict[str, DiscussionSource] = {s.key: s for s in (NexisSource(),)}


def label(key: str) -> str:
    src = SOURCES.get(key)
    return src.label if src else key


def describe() -> list[dict[str, object]]:
    return [{"key": s.key, "label": s.label, "native": s.native} for s in SOURCES.values()]
