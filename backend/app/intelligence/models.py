"""Pydantic models for AI output. Anything the model returns is validated here
before Python reconciles it with the registry; invalid output is rejected."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["podcast", "book", "event", "personal", "other"]


def _clean(s: str) -> str:
    return " ".join(str(s).split())


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    surface: str = Field(default="", max_length=300)  # text exactly as it appears in the note
    evidence: str = Field(default="", max_length=600)  # verbatim excerpt (sentence) from the note
    parent: str | None = Field(default=None, max_length=200)  # show for episodes, series for events
    aliases: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.7, ge=0, le=1)
    ambiguous: bool = False
    primary_subject: bool = False  # the note is mainly about this entity

    @field_validator("name", "surface", "evidence")
    @classmethod
    def _ws(cls, v: str) -> str:
        return _clean(v)

    @field_validator("parent")
    @classmethod
    def _parent(cls, v: str | None) -> str | None:
        v = _clean(v) if v else None
        return v or None

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        return [a for a in (_clean(x)[:200] for x in v) if a]


class ExtractedRelation(BaseModel):
    source: str = Field(min_length=1, max_length=200)  # entity name from `entities`
    target: str = Field(min_length=1, max_length=200)
    type: Literal["related_to", "belongs_to"] = "related_to"
    label: str = Field(default="", max_length=60)  # e.g. "author of", "speaker at"
    evidence: str = Field(default="", max_length=600)
    confidence: float = Field(default=0.6, ge=0, le=1)


class RelatedNote(BaseModel):
    note_ref: str  # the candidate's ref value from the prompt
    reason: str = Field(default="", max_length=300)
    evidence: str = Field(default="", max_length=600)
    confidence: float = Field(default=0.5, ge=0, le=1)


class NoteAnalysis(BaseModel):
    note_category: Category = "other"
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=100)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=100)
    related_notes: list[RelatedNote] = Field(default_factory=list, max_length=10)
