from app.models.paper import FullTextCandidate
from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult, SearchProvider
from app.utils.ids import normalize_doi, normalize_pmcid


class EuropePMCProvider(SearchProvider):
    name = "europepmc"
    search_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    fulltext_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        query = request.query
        if request.from_year or request.to_year:
            query += f" AND FIRST_PDATE:[{request.from_year or 1800} TO {request.to_year or 3000}]"
        if request.require_full_text:
            query += " AND OPEN_ACCESS:Y"
        response = await self.client.get(
            self.search_url,
            params={"query": query, "format": "json", "pageSize": request.max_results, "resultType": "core"},
        )
        response.raise_for_status()
        return [self._parse(item) for item in response.json().get("resultList", {}).get("result", []) if item.get("title")]

    async def fetch_xml(self, pmcid: str) -> tuple[bytes, str]:
        pmcid = normalize_pmcid(pmcid)
        if not pmcid:
            raise ValueError("A valid PMCID matching ^PMC\\d+$ is required")
        url = self.fulltext_url.format(pmcid=pmcid)
        response = await self.client.get(url)
        response.raise_for_status()
        return response.content, str(response.url)

    async def find_by_doi(self, doi: str) -> RawPaperResult | None:
        normalized = normalize_doi(doi)
        response = await self.client.get(
            self.search_url,
            params={
                "query": f'DOI:"{normalized}"',
                "format": "json",
                "pageSize": 1,
                "resultType": "core",
            },
        )
        response.raise_for_status()
        results = response.json().get("resultList", {}).get("result", [])
        return self._parse(results[0]) if results else None

    async def find_by_pmcid(self, pmcid: str) -> RawPaperResult | None:
        normalized = normalize_pmcid(pmcid)
        if not normalized:
            return None
        response = await self.client.get(
            self.search_url,
            params={
                "query": f"PMC_ID:{normalized}",
                "format": "json",
                "pageSize": 1,
                "resultType": "core",
            },
        )
        response.raise_for_status()
        results = response.json().get("resultList", {}).get("result", [])
        return self._parse(results[0]) if results else None

    def _parse(self, item: dict) -> RawPaperResult:
        pmcid = normalize_pmcid(item.get("pmcid"))
        candidates = []
        if pmcid:
            candidates.append(FullTextCandidate(
                source="europepmc", format="xml", priority=1,
                url=self.fulltext_url.format(pmcid=pmcid)
            ))
        author_list = item.get("authorList", {}).get("author", [])
        authors = [
            a.get("fullName") or " ".join(filter(None, [a.get("firstName"), a.get("lastName")]))
            for a in author_list
        ]
        return RawPaperResult(
            source=self.name,
            title=item["title"],
            abstract=item.get("abstractText"),
            year=int(item["pubYear"]) if str(item.get("pubYear", "")).isdigit() else None,
            journal=item.get("journalTitle"),
            authors=[a for a in authors if a],
            doi=normalize_doi(item.get("doi")),
            pmid=item.get("pmid"),
            pmcid=pmcid,
            citation_count=item.get("citedByCount"),
            is_open_access=item.get("isOpenAccess") == "Y" or bool(pmcid),
            full_text_candidates=candidates,
        )
