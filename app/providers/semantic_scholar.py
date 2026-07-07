from app.models.paper import FullTextCandidate
from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult, SearchProvider
from app.utils.ids import normalize_doi, normalize_pmcid


class SemanticScholarProvider(SearchProvider):
    name = "semantic_scholar"
    search_url = "https://api.semanticscholar.org/graph/v1/paper/search"

    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        headers = {}
        if self.settings.semantic_scholar_api_key:
            headers["x-api-key"] = self.settings.semantic_scholar_api_key
        fields = "paperId,title,abstract,year,venue,citationCount,authors,externalIds,openAccessPdf"
        response = await self.client.get(
            self.search_url,
            params={"query": request.query, "limit": request.max_results, "fields": fields},
            headers=headers,
        )
        response.raise_for_status()
        papers = [self._parse(item) for item in response.json().get("data", []) if item.get("title")]
        return [
            p for p in papers
            if (not request.from_year or not p.year or p.year >= request.from_year)
            and (not request.to_year or not p.year or p.year <= request.to_year)
            and (not request.require_full_text or p.full_text_candidates)
        ]

    def _parse(self, item: dict) -> RawPaperResult:
        ids = item.get("externalIds") or {}
        pdf = item.get("openAccessPdf") or {}
        candidates = []
        if pdf.get("url"):
            candidates.append(FullTextCandidate(
                source=self.name, format="pdf", priority=50, url=pdf["url"]
            ))
        return RawPaperResult(
            source=self.name,
            title=item["title"],
            abstract=item.get("abstract"),
            year=item.get("year"),
            journal=item.get("venue"),
            authors=[a["name"] for a in item.get("authors", []) if a.get("name")],
            doi=normalize_doi(ids.get("DOI")),
            pmid=ids.get("PubMed"),
            pmcid=normalize_pmcid(ids.get("PubMedCentral")),
            semantic_scholar_id=item.get("paperId"),
            citation_count=item.get("citationCount"),
            is_open_access=bool(pdf.get("url")),
            full_text_candidates=candidates,
        )
