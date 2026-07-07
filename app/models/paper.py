from pydantic import BaseModel, Field


class FullTextCandidate(BaseModel):
    source: str
    format: str
    access_type: str = "open"
    priority: int = 100
    url: str | None = None
    license: str | None = None


class PaperMetadata(BaseModel):
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
    full_text_candidates: list[FullTextCandidate] = Field(default_factory=list)
    source_hits: list[str] = Field(default_factory=list)
    field_sources: dict[str, list[str]] | None = None
    metadata_conflicts: list[dict[str, str | None]] | None = None
    score: float = 0.0
    query_relevance: float = 0.0
    biomedical_score: float = 0.0
