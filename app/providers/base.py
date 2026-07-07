from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.models.paper import FullTextCandidate, PaperMetadata
from app.models.requests import ScholarSearchRequest
from app.utils.ids import paper_id


class RawPaperResult(BaseModel):
    source: str
    title: str
    abstract: str | None = None
    year: int | None = None
    journal: str | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    published_doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    arxiv_id: str | None = None
    publication_date: str | None = None
    server: str | None = None
    category: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    is_preprint: bool = False
    peer_reviewed: bool | None = None
    review_status: str | None = None
    citation_count: int | None = None
    is_open_access: bool = False
    full_text_candidates: list[FullTextCandidate] = Field(default_factory=list)

    def normalize(self) -> PaperMetadata:
        values = self.model_dump(exclude={"source"})
        field_sources = {
            field: [self.source]
            for field, value in values.items()
            if value not in (None, "", [], {})
            and field not in {"full_text_candidates"}
        }
        return PaperMetadata(
            paper_id=paper_id(
                self.doi, self.pmid, self.pmcid, self.openalex_id,
                self.title, self.arxiv_id,
            ),
            **values,
            has_full_text=bool(self.full_text_candidates),
            source_hits=[self.source],
            field_sources=field_sources,
            metadata_conflicts=[],
        )


class SearchProvider(ABC):
    name: str

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    @abstractmethod
    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        raise NotImplementedError
