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
        # Main topics: on by default. Each becomes one node that links every note mentioning it.
        EntityType("book", "Book", "Books", "Books", description="A specific book (title of a published book)."),
        EntityType(
            "podcast_show", "Podcast", "Podcasts", "Podcasts",
            description="A podcast (the show as a whole, e.g. 'The Knowledge Project', 'Design Matters'). "
            "For a note about one episode, extract the show it belongs to.",
        ),
        EntityType(
            "event_series", "Event", "Events", "Design Events",
            description="An event, conference, meetup or community (e.g. 'Design Days', 'Friends of Figma'). "
            "Use its general name without year or edition ('Design Days', not 'Design Days 2026').",
        ),
        # Finer-grained types: off by default to keep the graph to main topics (people's names are not nodes).
        EntityType(
            "podcast_episode", "Podcast episode", "Podcast Episodes", "Podcasts", parent_type="podcast_show",
            description="One specific episode of a podcast. Set parent to the show name if known.",
            enabled_by_default=False,
        ),
        EntityType(
            "person", "Person", "People", None, plural="People",
            description="A named real person: designer, author, speaker, host, guest.",
            enabled_by_default=False,
        ),
        EntityType(
            "event", "Event edition", "Event Editions", "Design Events", parent_type="event_series",
            description="One specific edition or session of an event. Set parent to the event's general name.",
            enabled_by_default=False,
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
