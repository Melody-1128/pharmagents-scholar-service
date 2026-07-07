from pydantic import BaseModel, Field

from app.models.fulltext import RetrievalInfo
from app.models.paper import PaperMetadata
from app.models.requests import PaperFetchInput


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


class SearchPaperMetadata(BaseModel):
    paper_id: str = ""
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
    has_full_text: bool = False


class PaperSearchResponse(BaseModel):
    query: str
    total: int
    papers: list[SearchPaperMetadata]
    warnings: list[str] = Field(default_factory=list)


class ScholarFetchResponse(BaseModel):
    paper: PaperMetadata
    full_text_status: str
    retrieval: RetrievalInfo | None = None
    content: str = ""
    warnings: list[str] = Field(default_factory=list)


class ScholarFetchResult(ScholarFetchResponse):
    input: PaperFetchInput
    error: str | None = None


class ScholarFetchBatchResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[ScholarFetchResult] = Field(default_factory=list)


class PipelineItem(BaseModel):
    paper: PaperMetadata | SearchPaperMetadata
    fetch: ScholarFetchResponse | None = None
    error: str | None = None


class ScholarPipelineResponse(BaseModel):
    search: PaperSearchResponse
    fetched: list[PipelineItem]
    warnings: list[str] = Field(default_factory=list)
