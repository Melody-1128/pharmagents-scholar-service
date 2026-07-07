from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.ids import normalize_arxiv_id, normalize_pmcid


SearchSource = Literal[
    "openalex", "europepmc", "semantic_scholar", "pubmed",
    "biorxiv", "medrxiv", "arxiv",
]


class ScholarSearchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "query": "KRAS G12C sotorasib resistance mechanisms",
            "max_results": 10,
            "sources": [
                "openalex", "europepmc", "semantic_scholar", "pubmed",
                "biorxiv", "medrxiv", "arxiv",
            ],
            "from_year": 2020,
            "to_year": 2026,
        }]
    })

    query: str = Field(min_length=2)
    max_results: int = Field(default=10, ge=1, le=30)
    sources: list[SearchSource] = Field(
        default_factory=lambda: [
            "openalex", "europepmc", "semantic_scholar", "pubmed",
            "biorxiv", "medrxiv", "arxiv",
        ]
    )
    from_year: int | None = Field(default=None, ge=1800)
    to_year: int | None = Field(default=None, ge=1800)
    require_full_text: bool = False
    biomedical_only: bool = True

    @model_validator(mode="after")
    def validate_years(self):
        if self.to_year and self.to_year > datetime.now().year + 1:
            raise ValueError("to_year is implausibly far in the future")
        if self.from_year and self.to_year and self.from_year > self.to_year:
            raise ValueError("from_year must be <= to_year")
        return self


class PaperFetchInput(BaseModel):
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    arxiv_id: str | None = None
    title: str | None = None
    abstract: str | None = None
    source: str | None = None
    category: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    is_preprint: bool = False

    @field_validator("pmcid", mode="before")
    @classmethod
    def validate_pmcid(cls, value):
        return normalize_pmcid(value)

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def validate_arxiv_id(cls, value):
        return normalize_arxiv_id(value)

    @model_validator(mode="after")
    def require_identifier(self):
        if getattr(self, "papers", None):
            return self
        if not any([
            self.doi, self.pmid, self.pmcid, self.openalex_id,
            self.arxiv_id, self.title,
        ]):
            raise ValueError("Provide at least one identifier or title")
        return self


class ScholarFetchRequest(PaperFetchInput):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "doi": None,
                "pmid": None,
                "pmcid": "PMC9715446",
                "openalex_id": None,
                "semantic_scholar_id": None,
                "arxiv_id": None,
                "title": None,
                "abstract": None,
                "source": None,
                "category": None,
                "landing_url": None,
                "pdf_url": None,
                "is_preprint": False,
                "prefer_formats": ["xml", "html", "pdf"],
                "allow_pdf": True,
                "max_chars": 50000,
            },
            {
                "papers": [
                    {"doi": "10.1000/example"},
                    {"pmcid": "PMC9715446"},
                    {"pmid": "36315377"},
                ],
                "prefer_formats": ["xml", "html", "pdf"],
                "allow_pdf": True,
                "max_chars_per_paper": 50000,
            },
        ]
    })

    papers: list[PaperFetchInput] | None = None
    prefer_formats: list[Literal["xml", "html", "pdf"]] = Field(
        default_factory=lambda: ["xml", "html", "pdf"]
    )
    allow_pdf: bool = True
    max_chars: int = Field(default=50_000, ge=1_000, le=1_000_000)
    max_chars_per_paper: int | None = Field(default=None, ge=1_000, le=1_000_000)

    @model_validator(mode="after")
    def require_identifier_or_batch(self):
        if self.papers:
            return self
        if not any([
            self.doi, self.pmid, self.pmcid, self.openalex_id,
            self.arxiv_id, self.title,
        ]):
            raise ValueError("Provide papers or at least one identifier/title")
        return self

    def single_paper_request(self, paper: PaperFetchInput) -> "ScholarFetchRequest":
        return ScholarFetchRequest(
            **paper.model_dump(),
            prefer_formats=self.prefer_formats,
            allow_pdf=self.allow_pdf,
            max_chars=self.max_chars_per_paper or self.max_chars,
        )


class ScholarPipelineRequest(BaseModel):
    query: str = Field(min_length=2)
    max_search_results: int = Field(default=10, ge=1, le=30)
    fetch_top_n: int = Field(default=3, ge=0, le=20)
    require_full_text: bool = True
