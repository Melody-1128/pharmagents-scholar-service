from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class FullTextSection(BaseModel):
    section_type: Literal[
        "abstract", "introduction", "methods", "results", "discussion",
        "conclusion", "references", "other"
    ] = "other"
    heading: str
    text: str


class RetrievalInfo(BaseModel):
    source: str
    format: str
    license: str | None = None
    url: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
