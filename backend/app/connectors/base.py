from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field


class NoteSummary(BaseModel):
    id: str
    title: str
    folder: str = ""
    modified_at: str | None = None
    created_at: str | None = None


class Attachment(BaseModel):
    id: str = ""
    name: str
    content_type: str = ""
    data_b64: str | None = None  # present when the connector could export the bytes
    url: str | None = None  # for link attachments
    error: str | None = None


class SourceNote(NoteSummary):
    body: str = ""  # raw body as provided by the source
    body_format: Literal["html", "markdown", "text"] = "html"
    locked: bool = False  # password-protected notes cannot be read
    attachments: list[Attachment] = Field(default_factory=list)


class FolderInfo(BaseModel):
    account: str
    path: str
    note_count: int | None = None


class ConnectorError(RuntimeError):
    pass


class Connector(ABC):
    """Read-only access to a note source. Implementations must never write to it."""

    name: str = "base"

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...

    @abstractmethod
    def list_folders(self) -> list[FolderInfo]: ...

    @abstractmethod
    def list_notes(self) -> list[NoteSummary]: ...

    @abstractmethod
    def fetch(self, ids: list[str]) -> list[SourceNote]: ...
