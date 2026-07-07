import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from app.models.paper import FullTextCandidate
from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult, SearchProvider
from app.utils.ids import normalize_doi, normalize_title


class RxivProvider(SearchProvider):
    """Official bioRxiv/medRxiv metadata feed with local keyword filtering."""

    server: str
    base_url = "https://api.biorxiv.org/details"

    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        interval = self._interval(request)
        response = await self.client.get(f"{self.base_url}/{self.server}/{interval}/0")
        response.raise_for_status()
        papers = [
            self._parse(item)
            for item in response.json().get("collection", [])
            if item.get("title")
        ]
        papers = [
            paper for paper in papers
            if self._matches(request.query, paper.title, paper.abstract or "")
        ]
        papers.sort(
            key=lambda paper: self._match_score(
                request.query, paper.title, paper.abstract or ""
            ),
            reverse=True,
        )
        return papers[: request.max_results]

    async def fetch_by_doi(self, doi: str) -> RawPaperResult | None:
        normalized = normalize_doi(doi)
        response = await self.client.get(
            f"{self.base_url}/{self.server}/{normalized}/na/json"
        )
        response.raise_for_status()
        collection = response.json().get("collection", [])
        if not collection:
            return None
        paper = self._parse(collection[0])
        if paper.landing_url:
            try:
                discovered = await self._discover_assets(paper.landing_url)
                paper.full_text_candidates = self._merge_candidates(
                    discovered, paper.full_text_candidates
                )
                pdf = next(
                    (c.url for c in discovered if c.format == "pdf" and c.url), None
                )
                paper.pdf_url = pdf or paper.pdf_url
            except Exception:
                pass
        return paper

    def _interval(self, request: ScholarSearchRequest) -> str:
        if request.from_year or request.to_year:
            current_year = datetime.now().year
            start = request.from_year or max(1991, (request.to_year or current_year) - 1)
            end = request.to_year or current_year
            return f"{start}-01-01/{end}-12-31"
        return "30d"

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        return {
            term for term in normalize_title(query).split()
            if term not in {"and", "or", "not"}
        }

    @classmethod
    def _matches(cls, query: str, title: str, abstract: str) -> bool:
        terms = cls._query_terms(query)
        haystack = set(normalize_title(f"{title} {abstract}").split())
        return not terms or bool(terms & haystack)

    @classmethod
    def _match_score(cls, query: str, title: str, abstract: str) -> float:
        terms = cls._query_terms(query)
        if not terms:
            return 0
        title_words = set(normalize_title(title).split())
        all_words = title_words | set(normalize_title(abstract).split())
        return (
            0.75 * len(terms & title_words) / len(terms)
            + 0.25 * len(terms & all_words) / len(terms)
        )

    def _parse(self, item: dict) -> RawPaperResult:
        doi = normalize_doi(item.get("doi"))
        published_value = item.get("published")
        published = (
            normalize_doi(published_value)
            if published_value and str(published_value).lower() not in {"na", "none"}
            else None
        )
        date = item.get("date")
        landing_url = f"https://www.{self.server}.org/content/{doi}" if doi else None
        pdf_url = f"{landing_url}.full.pdf" if landing_url else None
        candidates = []
        seen_xml_urls = set()
        for key in ("jatsxml", "jats_xml", "tdmxml", "tdm_xml", "xml_url"):
            xml_value = item.get(key)
            if not xml_value:
                continue
            xml_url = self._normalize_asset_url(
                urljoin(f"https://www.{self.server}.org", str(xml_value))
            )
            if not xml_url:
                continue
            if xml_url in seen_xml_urls:
                continue
            seen_xml_urls.add(xml_url)
            candidates.append(FullTextCandidate(
                source=self.server,
                format="xml",
                priority=10,
                url=xml_url,
                license=item.get("license"),
            ))
        if landing_url:
            candidates.append(FullTextCandidate(
                source=self.server,
                format="html",
                priority=20,
                url=landing_url,
                license=item.get("license"),
            ))
        if pdf_url:
            candidates.append(FullTextCandidate(
                source=self.server,
                format="pdf",
                priority=30,
                url=pdf_url,
                license=item.get("license"),
            ))
        authors = [
            name.strip()
            for name in re.split(r";|\|", item.get("authors") or "")
            if name.strip()
        ]
        return RawPaperResult(
            source=self.server,
            server=self.server,
            title=item["title"],
            abstract=item.get("abstract"),
            authors=authors,
            publication_date=date,
            year=int(date[:4]) if date and date[:4].isdigit() else None,
            doi=doi,
            published_doi=published,
            journal=self.server,
            category=item.get("category"),
            landing_url=landing_url,
            pdf_url=pdf_url,
            is_open_access=True,
            is_preprint=True,
            peer_reviewed=False,
            review_status="preprint",
            full_text_candidates=candidates,
        )

    async def _discover_assets(self, landing_url: str) -> list[FullTextCandidate]:
        response = await self.client.get(landing_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "lxml")
        found: list[FullTextCandidate] = []
        selectors = [
            ("meta[name='citation_xml_url']", "content", "xml", 5),
            ("meta[name='citation_pdf_url']", "content", "pdf", 25),
            ("link[type*='xml']", "href", "xml", 5),
            ("a[href$='.xml']", "href", "xml", 5),
            ("a[href*='.source.xml']", "href", "xml", 5),
            ("a[href$='.pdf']", "href", "pdf", 25),
        ]
        for selector, attribute, fmt, priority in selectors:
            for node in soup.select(selector):
                value = node.get(attribute)
                url = self._normalize_asset_url(urljoin(landing_url, value or ""))
                if url:
                    found.append(FullTextCandidate(
                        source=self.server, format=fmt, priority=priority, url=url
                    ))
        return self._merge_candidates(found)

    def _normalize_asset_url(self, value: str) -> str | None:
        parts = urlsplit(value)
        allowed_hosts = {
            f"www.{self.server}.org",
            self.server + ".org",
            "connect.biorxiv.org",
        }
        if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in allowed_hosts:
            return None
        path = re.sub(r"/{2,}", "/", parts.path)
        return urlunsplit(("https", parts.netloc.lower(), path, parts.query, ""))

    @staticmethod
    def _merge_candidates(*groups) -> list[FullTextCandidate]:
        merged = []
        seen = set()
        for group in groups:
            for candidate in group:
                key = (candidate.format, candidate.url)
                if candidate.url and key not in seen:
                    seen.add(key)
                    merged.append(candidate)
        return sorted(merged, key=lambda candidate: candidate.priority)


class BioRxivProvider(RxivProvider):
    name = "biorxiv"
    server = "biorxiv"
