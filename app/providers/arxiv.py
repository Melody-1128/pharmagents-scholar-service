from xml.etree import ElementTree as ET

from app.models.paper import FullTextCandidate
from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult, SearchProvider
from app.utils.ids import normalize_arxiv_id, normalize_doi
from app.utils.text import clean_text


ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"


class ArxivProvider(SearchProvider):
    name = "arxiv"
    server = "arxiv"
    search_url = "https://export.arxiv.org/api/query"

    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        query = f"all:({request.query})"
        if request.from_year or request.to_year:
            start = request.from_year or 1991
            end = request.to_year or 2999
            query += f" AND submittedDate:[{start}01010000 TO {end}12312359]"
        response = await self.client.get(
            self.search_url,
            params={
                "search_query": query,
                "start": 0,
                "max_results": request.max_results,
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
        )
        response.raise_for_status()
        return self._parse_feed(response.content)

    async def fetch_by_id(self, arxiv_id: str) -> RawPaperResult | None:
        arxiv_id = normalize_arxiv_id(arxiv_id)
        if not arxiv_id:
            return None
        response = await self.client.get(
            self.search_url, params={"id_list": arxiv_id, "max_results": 1}
        )
        response.raise_for_status()
        results = self._parse_feed(response.content)
        return results[0] if results else None

    def _parse_feed(self, content: bytes) -> list[RawPaperResult]:
        root = ET.fromstring(content)
        results = []
        for entry in root.findall(f"{{{ATOM}}}entry"):
            identifier = normalize_arxiv_id(
                (entry.findtext(f"{{{ATOM}}}id") or "").rsplit("/abs/", 1)[-1]
            )
            if not identifier:
                continue
            landing_url = None
            pdf_url = None
            for link in entry.findall(f"{{{ATOM}}}link"):
                if link.attrib.get("rel") == "alternate":
                    landing_url = link.attrib.get("href")
                if link.attrib.get("title") == "pdf":
                    pdf_url = link.attrib.get("href")
            landing_url = landing_url or f"https://arxiv.org/abs/{identifier}"
            pdf_url = pdf_url or f"https://arxiv.org/pdf/{identifier}"
            published = entry.findtext(f"{{{ATOM}}}published")
            category = None
            primary = entry.find(f"{{{ARXIV}}}primary_category")
            if primary is not None:
                category = primary.attrib.get("term")
            candidates = [FullTextCandidate(
                source="arxiv", format="pdf", priority=45, url=pdf_url
            )]
            results.append(RawPaperResult(
                source="arxiv",
                server="arxiv",
                title=clean_text(entry.findtext(f"{{{ATOM}}}title")) or identifier,
                abstract=clean_text(entry.findtext(f"{{{ATOM}}}summary")),
                authors=[
                    clean_text(author.findtext(f"{{{ATOM}}}name")) or ""
                    for author in entry.findall(f"{{{ATOM}}}author")
                    if author.findtext(f"{{{ATOM}}}name")
                ],
                publication_date=published,
                year=int(published[:4]) if published and published[:4].isdigit() else None,
                doi=normalize_doi(entry.findtext(f"{{{ARXIV}}}doi")),
                arxiv_id=identifier,
                journal="arXiv",
                category=category,
                landing_url=landing_url,
                pdf_url=pdf_url,
                is_open_access=True,
                is_preprint=True,
                peer_reviewed=False,
                review_status="preprint",
                full_text_candidates=candidates,
            ))
        return results
