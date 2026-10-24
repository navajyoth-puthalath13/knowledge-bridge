"""Entity type registry.

Adding a new entity type means adding an `EntityType` here (or via config);
the extraction prompt, resolver and renderer are all driven by this table.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityType:
    key: str
    label: str  # singular, shown in the UI and on pages
    folder: str  # under <generated>/Entities/
    collection: str | None  # collection a top-level entity belongs to
    parent_type: str | None = None  # e.g. episode -> show; parent replaces the collection link
    description: str = ""  # used in the extraction prompt
    enabled_by_default: bool = True
    extractable: bool = True  # False for types created by the pipeline itself
    plural: str = ""

    @property
    def plural_label(self) -> str:
        return self.plural or self.label + "s"


COLLECTIONS = ["Books", "Podcasts", "Personal Thinking", "Design Events"]

ENTITY_TYPES: dict[str, EntityType] = {
    t.key: t
    for t in [
        EntityType("book", "Book", "Books", "Books", description="A specific book (title of a published book)."),
        EntityType(
            "podcast_show", "Podcast show", "Podcast Shows", "Podcasts",
            description="A podcast series/show as a whole (e.g. 'Design Matters').",
        ),
        EntityType(
            "podcast_episode", "Podcast episode", "Podcast Episodes", "Podcasts", parent_type="podcast_show",
            description="One specific episode of a podcast. Set parent to the show name if known.",
        ),
        EntityType(
            "person", "Person", "People", None, plural="People",
            description="A named real person: designer, author, speaker, host, guest.",
        ),
        EntityType(
            "event_series", "Event series", "Events", "Design Events", plural="Event series",
            description="A recurring event, meetup series or community (e.g. 'Friends of Figma', 'Design Days').",
        ),
        EntityType(
            "event", "Event", "Events", "Design Events", parent_type="event_series",
            description="One specific event, meetup, conference edition or session. Set parent to its series if known.",
        ),
        EntityType(
            "topic", "Topic", "Topics", None,
            description="Only a meaningful, reusable named concept or framework (e.g. 'flow state', 'Jobs to be Done'). "
            "Never ordinary words.",
            enabled_by_default=False,
        ),
    ]
}

# Internal types (not extractable, not in the enable/disable list).
NOTE_TYPE = "note"  # a mirrored source note; its "page" is the mirror file itself
COLLECTION_TYPE = "collection"

# Which collection a note of each category is organised under.
CATEGORY_COLLECTION = {
    "personal": "Personal Thinking",
}


def enabled_types(overrides: dict[str, bool]) -> list[EntityType]:
    return [t for t in ENTITY_TYPES.values() if overrides.get(t.key, t.enabled_by_default)]
