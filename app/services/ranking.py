import math
from datetime import datetime

from app.models.paper import PaperMetadata
from app.utils.ids import normalize_title


SOURCE_CONFIDENCE = {
    "pubmed": 1.0,
    "europepmc": 0.95,
    "openalex": 0.85,
    "semantic_scholar": 0.8,
    "biorxiv": 0.75,
    "medrxiv": 0.75,
    "arxiv": 0.72,
}

PREPRINT_INTENT_TERMS = {
    "latest", "recent", "new", "newest", "preprint",
    "new model", "ai method", "novel method", "最新", "预印本", "新模型", "新方法",
}


def _relevance(query: str, paper: PaperMetadata) -> float:
    terms = {
        term for term in normalize_title(query).split()
        if term not in {"and", "or", "not"}
    }
    if not terms:
        return 0.0
    title = set(normalize_title(paper.title).split())
    abstract = set(normalize_title(paper.abstract or "").split())
    title_hit = len(terms & title) / len(terms)
    any_hit = len(terms & (title | abstract)) / len(terms)
    return min(1.0, 0.7 * title_hit + 0.3 * any_hit)


def rank_papers(query: str, papers: list[PaperMetadata]) -> list[PaperMetadata]:
    current_year = datetime.now().year
    normalized_query = normalize_title(query)
    raw_query = query.lower()
    preprint_intent = any(
        (
            (normalized_term := normalize_title(term))
            and normalized_term in normalized_query
        )
        or term.lower() in raw_query
        for term in PREPRINT_INTENT_TERMS
    )
    for paper in papers:
        relevance = _relevance(query, paper)
        confidence = max((SOURCE_CONFIDENCE.get(x, 0.5) for x in paper.source_hits), default=0.5)
        fulltext = 1.0 if paper.has_full_text else 0.5 if paper.is_open_access else 0.0
        recency = max(0.0, 1 - max(0, current_year - (paper.year or current_year - 15)) / 15)
        citation = min(1.0, math.log1p(paper.citation_count or 0) / math.log1p(1000))
        biomedical = 1.0 if any(
            x in paper.source_hits
            for x in ("pubmed", "europepmc", "biorxiv", "medrxiv")
        ) else 0.5
        paper.query_relevance = relevance
        paper.biomedical_score = biomedical
        base_score = (
            0.35 * relevance
            + 0.20 * confidence
            + 0.15 * fulltext
            + 0.10 * recency
            + 0.10 * citation
            + 0.10 * biomedical
        )
        preprint_adjustment = (0.06 if preprint_intent else -0.08) if paper.is_preprint else 0
        paper.score = round(max(0.0, min(1.0, base_score + preprint_adjustment)), 4)
    return sorted(papers, key=lambda p: p.score, reverse=True)
