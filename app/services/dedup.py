from difflib import SequenceMatcher

from app.models.paper import PaperMetadata
from app.utils.ids import normalize_doi, normalize_pmcid, normalize_title, paper_id


PROVIDER_PRIORITY = {
    "europepmc": 100,
    "pubmed": 95,
    "crossref": 85,
    "openalex": 80,
    "semantic_scholar": 75,
    "biorxiv": 70,
    "medrxiv": 70,
    "arxiv": 65,
}

SCALAR_FIELDS = [
    "title", "abstract", "year", "journal", "doi", "published_doi", "pmid",
    "pmcid", "openalex_id", "semantic_scholar_id", "arxiv_id",
    "publication_date", "server", "category", "landing_url", "pdf_url",
    "citation_count",
]


def _normalized_id(field: str, value):
    if field in {"doi", "published_doi"}:
        return normalize_doi(value)
    if field == "pmcid":
        return normalize_pmcid(value)
    return str(value).strip().lower() if value else None


def _identifier_conflict(a: PaperMetadata, b: PaperMetadata) -> bool:
    for field in ("doi", "pmid", "pmcid", "arxiv_id"):
        left = _normalized_id(field, getattr(a, field))
        right = _normalized_id(field, getattr(b, field))
        if left and right and left != right:
            # A preprint DOI may legitimately differ from its published DOI.
            if field == "doi" and (
                _normalized_id("doi", a.published_doi) == right
                or _normalized_id("doi", b.published_doi) == left
            ):
                continue
            return True
    return False


def _shared_strong_identifier(a: PaperMetadata, b: PaperMetadata) -> bool:
    identifier_pairs = [
        (a.doi, b.doi, "doi"),
        (a.published_doi, b.doi, "doi"),
        (a.doi, b.published_doi, "doi"),
        (a.published_doi, b.published_doi, "doi"),
        (a.pmid, b.pmid, "pmid"),
        (a.pmcid, b.pmcid, "pmcid"),
        (a.arxiv_id, b.arxiv_id, "arxiv_id"),
    ]
    return any(
        left and right and _normalized_id(field, left) == _normalized_id(field, right)
        for left, right, field in identifier_pairs
    )


def _author_overlap(a: PaperMetadata, b: PaperMetadata) -> bool:
    def surnames(authors):
        return {
            normalize_title(author).split()[-1]
            for author in authors
            if normalize_title(author).split()
        }
    left, right = surnames(a.authors), surnames(b.authors)
    return bool(left and right and left & right)


def _same(a: PaperMetadata, b: PaperMetadata) -> bool:
    if _identifier_conflict(a, b):
        return False
    if _shared_strong_identifier(a, b):
        return True
    ta, tb = normalize_title(a.title), normalize_title(b.title)
    if not ta or not tb or SequenceMatcher(None, ta, tb).ratio() < 0.97:
        return False
    year_compatible = not a.year or not b.year or abs(a.year - b.year) <= 1
    return year_compatible and _author_overlap(a, b)


def _best_source(paper: PaperMetadata, field: str) -> str | None:
    sources = (paper.field_sources or {}).get(field) or paper.source_hits
    return max(sources, key=lambda source: PROVIDER_PRIORITY.get(source, 0), default=None)


def _record_conflict(
    target: PaperMetadata,
    field: str,
    kept_value,
    kept_source: str | None,
    rejected_value,
    rejected_source: str | None,
) -> None:
    target.metadata_conflicts = target.metadata_conflicts or []
    target.metadata_conflicts.append({
        "field": field,
        "kept_value": str(kept_value) if kept_value is not None else None,
        "kept_source": kept_source,
        "rejected_value": str(rejected_value) if rejected_value is not None else None,
        "rejected_source": rejected_source,
    })


def _merge_scalar(target: PaperMetadata, incoming: PaperMetadata, field: str) -> None:
    current, new = getattr(target, field), getattr(incoming, field)
    if new in (None, ""):
        return
    incoming_sources = (incoming.field_sources or {}).get(field) or incoming.source_hits
    target.field_sources = target.field_sources or {}
    if current in (None, ""):
        setattr(target, field, new)
        target.field_sources[field] = list(dict.fromkeys(incoming_sources))
        return
    current_normalized = _normalized_id(field, current) if field in {
        "doi", "published_doi", "pmid", "pmcid", "arxiv_id"
    } else current
    new_normalized = _normalized_id(field, new) if field in {
        "doi", "published_doi", "pmid", "pmcid", "arxiv_id"
    } else new
    if current_normalized == new_normalized:
        target.field_sources[field] = list(dict.fromkeys(
            target.field_sources.get(field, []) + incoming_sources
        ))
        return
    if field == "abstract" and len(str(new)) > len(str(current)):
        setattr(target, field, new)
        target.field_sources[field] = list(dict.fromkeys(incoming_sources))
        return
    if field == "citation_count":
        if int(new) > int(current):
            setattr(target, field, new)
            target.field_sources[field] = list(dict.fromkeys(incoming_sources))
        return
    current_source = _best_source(target, field)
    incoming_source = _best_source(incoming, field)
    if PROVIDER_PRIORITY.get(incoming_source or "", 0) > PROVIDER_PRIORITY.get(
        current_source or "", 0
    ):
        _record_conflict(
            target, field, new, incoming_source, current, current_source
        )
        setattr(target, field, new)
        target.field_sources[field] = list(dict.fromkeys(incoming_sources))
    else:
        _record_conflict(
            target, field, current, current_source, new, incoming_source
        )


def _merge(target: PaperMetadata, incoming: PaperMetadata) -> PaperMetadata:
    for field in SCALAR_FIELDS:
        _merge_scalar(target, incoming, field)
    target.authors = list(dict.fromkeys(target.authors + incoming.authors))
    target.source_hits = list(dict.fromkeys(target.source_hits + incoming.source_hits))
    if incoming.authors:
        target.field_sources = target.field_sources or {}
        target.field_sources["authors"] = list(dict.fromkeys(
            target.field_sources.get("authors", [])
            + ((incoming.field_sources or {}).get("authors") or incoming.source_hits)
        ))
    seen_candidates = {(c.source, c.format, c.url) for c in target.full_text_candidates}
    target.full_text_candidates.extend(
        c for c in incoming.full_text_candidates
        if (c.source, c.format, c.url) not in seen_candidates
    )
    target.full_text_candidates.sort(key=lambda c: c.priority)
    target.is_open_access = target.is_open_access or incoming.is_open_access
    target.has_full_text = bool(target.full_text_candidates)
    if target.is_preprint and not incoming.is_preprint:
        if incoming.doi:
            target.published_doi = target.published_doi or incoming.doi
            target.doi = incoming.doi
        target.is_preprint = False
        target.peer_reviewed = True
        target.review_status = "published"
    elif not target.is_preprint and incoming.is_preprint:
        target.peer_reviewed = True
        target.review_status = target.review_status or "published"
    else:
        target.is_preprint = target.is_preprint or incoming.is_preprint
        if target.is_preprint:
            target.peer_reviewed = False
            target.review_status = "preprint"
    target.paper_id = paper_id(
        target.doi, target.pmid, target.pmcid, target.openalex_id,
        target.title, target.arxiv_id,
    )
    return target


def deduplicate(papers: list[PaperMetadata]) -> list[PaperMetadata]:
    merged: list[PaperMetadata] = []
    for paper in papers:
        match = next((existing for existing in merged if _same(existing, paper)), None)
        if match:
            _merge(match, paper)
        else:
            merged.append(paper.model_copy(deep=True))
    return merged
