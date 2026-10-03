"""Where Nexis Pulse discussions come from.

Every discussion row carries a ``source`` key:

* ``nexis`` — **Nexis Community**: written by members. Only these count toward the community Pulse score and
  sentiment.
* ``research`` — **Nexis Research**: editorial discussions by the official Nexis Research account. They appear in
  lists and topics with their own label and form a separate "research view", never community sentiment.
* ``generated`` — **Nexis Perspectives**: discussions written by Nexis-generated (AI) personas about real events.
  Always labelled as generated; never counted as community sentiment.

A permitted external source would be added by writing a class with the same attributes, registering it in
``SOURCES`` and storing its items under its own key; scoring and pages work on the stored rows.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscussionSource:
    key: str
    label: str
    # Written by members, so it counts toward the community Pulse score.
    community: bool
    # Published by Nexis itself (editorial), shown with an official label.
    editorial: bool
    # Written by Nexis-generated personas, labelled as generated.
    generated: bool = False


NEXIS_COMMUNITY = DiscussionSource(key="nexis", label="Nexis Community", community=True, editorial=False)
NEXIS_RESEARCH = DiscussionSource(key="research", label="Nexis Research", community=False, editorial=True)
NEXIS_GENERATED = DiscussionSource(key="generated", label="Nexis Perspectives", community=False, editorial=False, generated=True)

SOURCES: dict[str, DiscussionSource] = {s.key: s for s in (NEXIS_COMMUNITY, NEXIS_RESEARCH, NEXIS_GENERATED)}
COMMUNITY: tuple[str, ...] = tuple(k for k, s in SOURCES.items() if s.community)
EDITORIAL: tuple[str, ...] = tuple(k for k, s in SOURCES.items() if s.editorial)
GENERATED: tuple[str, ...] = tuple(k for k, s in SOURCES.items() if s.generated)


def label(key: str) -> str:
    src = SOURCES.get(key)
    return src.label if src else key


def describe() -> list[dict[str, object]]:
    return [
        {"key": s.key, "label": s.label, "community": s.community, "editorial": s.editorial, "generated": s.generated}
        for s in SOURCES.values()
    ]
