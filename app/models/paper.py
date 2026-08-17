from pydantic import BaseModel, Field, computed_field

# Formats the current fetch pipeline knows how to parse.
_SUPPORTED_FETCH_FORMATS = {"xml", "html", "pdf"}


class FullTextCandidate(BaseModel):
    source: str
    format: str
    access_type: str = "open"
    priority: int = 100
    url: str | None = None
    license: str | None = None

    def is_fetchable(self) -> bool:
        """Whether the current fetch pipeline would attempt this candidate.

        A conservative, provider-agnostic gate: the access check must pass
        (``open``) and the format must be one the parsers support. It does NOT
        promise the fetch will succeed — only ``full_text_status == "success"``
        means full text was actually retrieved and parsed.
        """
        return (
            self.access_type == "open"
            and self.format in _SUPPORTED_FETCH_FORMATS
            and bool(self.url)
        )


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
    # Legacy flag kept for backward compatibility. Historically it meant
    # "a full-text candidate exists" (bool(full_text_candidates)); it never
    # guaranteed the pipeline could actually retrieve the text. Prefer the
    # explicit computed flags below. See INFORMATION_SOURCE_2 report.
    has_full_text: bool = False
    full_text_candidates: list[FullTextCandidate] = Field(default_factory=list)
    source_hits: list[str] = Field(default_factory=list)
    field_sources: dict[str, list[str]] | None = None
    metadata_conflicts: list[dict[str, str | None]] | None = None
    score: float = 0.0
    query_relevance: float = 0.0
    biomedical_score: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_full_text_candidate(self) -> bool:
        """At least one full-text candidate was discovered by search/retrieval.

        This is the honest meaning of the legacy ``has_full_text`` flag. It does
        NOT mean the text is obtainable — only that a candidate link exists.
        """
        return bool(self.full_text_candidates)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_text_retrievable(self) -> bool:
        """At least one candidate the current fetch pipeline would attempt.

        Still not a success guarantee; only ``full_text_status == "success"``
        confirms full text was retrieved and parsed.
        """
        return any(c.is_fetchable() for c in self.full_text_candidates)
