from app.models.paper import FullTextCandidate
from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult, SearchProvider
from app.utils.ids import normalize_doi
from app.utils.text import inverted_index_to_text


class OpenAlexProvider(SearchProvider):
    name = "openalex"
    base_url = "https://api.openalex.org/works"

    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        filters = []
        if request.from_year:
            filters.append(f"from_publication_date:{request.from_year}-01-01")
        if request.to_year:
            filters.append(f"to_publication_date:{request.to_year}-12-31")
        if request.require_full_text:
            filters.append("has_fulltext:true")
        params = {"search": request.query, "per-page": request.max_results}
        if filters:
            params["filter"] = ",".join(filters)
        if self.settings.openalex_api_key:
            params["api_key"] = self.settings.openalex_api_key
        elif self.settings.ncbi_email:
            params["mailto"] = self.settings.ncbi_email
        response = await self.client.get(self.base_url, params=params)
        response.raise_for_status()
        return [self._parse(item) for item in response.json().get("results", []) if item.get("title")]

    def _parse(self, item: dict) -> RawPaperResult:
        ids = item.get("ids") or {}
        oa = item.get("open_access") or {}
        primary = item.get("primary_location") or {}
        source = primary.get("source") or {}
        best = item.get("best_oa_location") or {}
        candidates = []
        if best.get("pdf_url"):
            candidates.append(FullTextCandidate(
                source="openalex", format="pdf", priority=40, url=best["pdf_url"],
                license=best.get("license")
            ))
        authors = [
            a.get("author", {}).get("display_name")
            for a in item.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ]
        return RawPaperResult(
            source=self.name,
            title=item["title"],
            abstract=inverted_index_to_text(item.get("abstract_inverted_index")),
            year=item.get("publication_year"),
            journal=source.get("display_name"),
            authors=authors,
            doi=normalize_doi(ids.get("doi")),
            pmid=(ids.get("pmid") or "").rsplit("/", 1)[-1] or None,
            pmcid=(ids.get("pmcid") or "").rsplit("/", 1)[-1] or None,
            openalex_id=(item.get("id") or "").rsplit("/", 1)[-1] or None,
            citation_count=item.get("cited_by_count"),
            is_open_access=bool(oa.get("is_oa")),
            full_text_candidates=candidates,
        )
