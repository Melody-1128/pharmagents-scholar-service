from app.models.paper import FullTextCandidate, PaperMetadata
from app.models.requests import ScholarFetchRequest, ScholarSearchRequest
from app.providers.crossref import CrossrefProvider
from app.providers.europepmc import EuropePMCProvider
from app.utils.ids import normalize_arxiv_id, normalize_pmcid


class MetadataResolver:
    def __init__(
        self,
        europepmc: EuropePMCProvider,
        crossref: CrossrefProvider,
        preprint_providers: dict | None = None,
    ) -> None:
        self.europepmc = europepmc
        self.crossref = crossref
        self.preprint_providers = preprint_providers or {}

    async def resolve(self, request: ScholarFetchRequest) -> PaperMetadata:
        arxiv_id = normalize_arxiv_id(request.arxiv_id)
        pmcid = normalize_pmcid(request.pmcid)
        source = request.source or ("arxiv" if arxiv_id else None)
        landing_url = request.landing_url
        pdf_url = request.pdf_url
        if arxiv_id:
            landing_url = landing_url or f"https://arxiv.org/abs/{arxiv_id}"
            pdf_url = pdf_url or f"https://arxiv.org/pdf/{arxiv_id}"
        candidates = []
        if pdf_url:
            candidates.append(FullTextCandidate(
                source=source or "preprint", format="pdf", priority=45, url=pdf_url
            ))
        base = PaperMetadata(
            title=request.title or request.doi or request.pmid or request.pmcid or "Unknown paper",
            abstract=request.abstract,
            doi=request.doi,
            pmid=request.pmid,
            pmcid=pmcid,
            openalex_id=request.openalex_id,
            semantic_scholar_id=request.semantic_scholar_id,
            arxiv_id=arxiv_id,
            server=source,
            category=request.category,
            landing_url=landing_url,
            pdf_url=pdf_url,
            is_preprint=request.is_preprint or bool(arxiv_id) or source in {
                "biorxiv", "medrxiv", "arxiv"
            },
            peer_reviewed=False if (
                request.is_preprint or arxiv_id
                or source in {"biorxiv", "medrxiv", "arxiv"}
            ) else None,
            review_status="preprint" if (
                request.is_preprint or arxiv_id
                or source in {"biorxiv", "medrxiv", "arxiv"}
            ) else None,
            is_open_access=bool(pdf_url),
            has_full_text=bool(pdf_url),
            full_text_candidates=candidates,
        )
        if arxiv_id and "arxiv" in self.preprint_providers:
            try:
                raw = await self.preprint_providers["arxiv"].fetch_by_id(arxiv_id)
                if raw:
                    return raw.normalize()
            except Exception:
                pass
        if request.doi and (
            base.is_preprint or normalize_preprint_doi(request.doi)
        ):
            names = (
                [source] if source in {"biorxiv", "medrxiv"}
                else ["biorxiv", "medrxiv"]
            )
            for name in names:
                provider = self.preprint_providers.get(name)
                if not provider:
                    continue
                try:
                    raw = await provider.fetch_by_doi(request.doi)
                    if raw:
                        return raw.normalize()
                except Exception:
                    continue
        if base.is_preprint:
            return base
        # Non-preprint enrichment: UNION all available metadata paths into `base`.
        # A DOI must NOT short-circuit other identifier/metadata enrichment. The
        # previous implementation returned the Crossref record early, which hid
        # the Europe PMC / PMCID (JATS full-text) discovery path whenever a DOI
        # was present, making fetch(doi) strictly worse than fetch(pmcid).
        if request.doi:
            try:
                raw, _ = await self.crossref.metadata(request.doi)
                if raw:
                    _merge(base, raw.normalize())
            except Exception:
                pass
        # Europe PMC discovery: only when we still lack a PMCID. If a PMCID is
        # already known the fetch service enriches from it directly, so this
        # avoids a redundant call. When missing, discovering it here keeps the
        # full-text path reachable for DOI-only / PMID-only / title-only inputs.
        if not normalize_pmcid(base.pmcid):
            query = None
            if base.pmid:
                query = f"EXT_ID:{base.pmid}"
            elif base.doi:
                query = f'DOI:"{base.doi}"'
            elif base.title and str(base.title).lower() not in {
                "unknown paper",
                str(base.doi or "").lower(),
                str(base.pmid or "").lower(),
                str(base.pmcid or "").lower(),
            }:
                query = f'TITLE:"{base.title}"'
            if query:
                try:
                    results = await self.europepmc.search(
                        ScholarSearchRequest(query=query, max_results=1, sources=["europepmc"])
                    )
                    if results:
                        _merge(base, results[0].normalize())
                except Exception:
                    pass
        return base


def normalize_preprint_doi(doi: str | None) -> bool:
    if not doi:
        return False
    normalized = doi.lower().replace("https://doi.org/", "")
    return normalized.startswith(("10.1101/", "10.64898/"))


# Fields whose values are enriched (filled/upgraded) but never overwritten by
# an empty/null incoming value.
_SCALAR_FIELDS = (
    "year", "journal", "publication_date", "peer_reviewed", "review_status",
    "citation_count", "category", "landing_url", "pdf_url",
)
# Identifier fields: union when the base value is missing; keep base on conflict.
_ID_FIELDS = (
    "doi", "pmid", "pmcid", "openalex_id", "semantic_scholar_id", "arxiv_id",
)
_PLACEHOLDER_TITLES = {"unknown paper", ""}


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _merge(base: PaperMetadata, incoming: PaperMetadata) -> None:
    """UNION/enrich `incoming` metadata into `base` in place.

    Rules (kept intentionally simple, no provenance engine):
    * empty base + non-empty incoming -> fill
    * non-empty base + empty incoming -> keep base (never null out)
    * abstract: prefer the richer (longer) non-empty text
    * title: replace placeholder base title with a real incoming title
    * identifiers: fill only when base is missing; conflicts keep base
    """
    if not _is_empty(incoming.abstract):
        if _is_empty(base.abstract) or len(incoming.abstract) > len(base.abstract or ""):
            base.abstract = incoming.abstract
    # A base title is a placeholder if it is the literal "Unknown paper" or if it
    # merely echoes one of the base's own identifiers (resolver seeds title from
    # doi/pmid/pmcid when no real title is known). In those cases a real incoming
    # title should win.
    base_title_norm = str(base.title).strip().lower()
    identifier_placeholders = {
        str(getattr(base, f, "") or "").strip().lower()
        for f in ("doi", "pmid", "pmcid", "openalex_id", "arxiv_id")
    }
    is_placeholder_title = (
        base_title_norm in _PLACEHOLDER_TITLES
        or base_title_norm in identifier_placeholders
    )
    if not _is_empty(incoming.title) and is_placeholder_title:
        base.title = incoming.title
    for field in _ID_FIELDS:
        incoming_val = getattr(incoming, field, None)
        if not _is_empty(incoming_val) and _is_empty(getattr(base, field, None)):
            setattr(base, field, incoming_val)
    for field in _SCALAR_FIELDS:
        incoming_val = getattr(incoming, field, None)
        if not _is_empty(incoming_val) and _is_empty(getattr(base, field, None)):
            setattr(base, field, incoming_val)
    if incoming.authors and not base.authors:
        base.authors = list(dict.fromkeys(incoming.authors))
    base.is_open_access = base.is_open_access or incoming.is_open_access
    seen = {(c.source, c.format, c.url) for c in base.full_text_candidates}
    for cand in incoming.full_text_candidates:
        if (cand.source, cand.format, cand.url) not in seen:
            base.full_text_candidates.append(cand)
            seen.add((cand.source, cand.format, cand.url))
    base.has_full_text = bool(base.full_text_candidates)
    base.source_hits = list(dict.fromkeys(base.source_hits + incoming.source_hits))
