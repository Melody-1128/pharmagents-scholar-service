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
        if request.doi and not base.is_preprint:
            try:
                raw, _ = await self.crossref.metadata(request.doi)
                if raw:
                    resolved = raw.normalize()
                    for field in ("pmid", "pmcid", "openalex_id", "semantic_scholar_id", "abstract"):
                        if getattr(base, field):
                            setattr(resolved, field, getattr(base, field))
                    return resolved
            except Exception:
                pass
        if request.title or request.pmid or pmcid:
            query = (
                f'EXT_ID:{request.pmid}' if request.pmid
                else f'PMC_ID:{pmcid}' if pmcid
                else f'TITLE:"{request.title}"'
            )
            try:
                results = await self.europepmc.search(
                    ScholarSearchRequest(query=query, max_results=1, sources=["europepmc"])
                )
                if results:
                    return results[0].normalize()
            except Exception:
                pass
        return base


def normalize_preprint_doi(doi: str | None) -> bool:
    if not doi:
        return False
    normalized = doi.lower().replace("https://doi.org/", "")
    return normalized.startswith(("10.1101/", "10.64898/"))
