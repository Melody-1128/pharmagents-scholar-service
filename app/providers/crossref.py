from app.models.paper import FullTextCandidate
from app.providers.base import RawPaperResult
from app.utils.ids import normalize_doi


class CrossrefProvider:
    def __init__(self, client, settings) -> None:
        self.client = client
        self.settings = settings

    async def metadata(self, doi: str) -> tuple[RawPaperResult | None, list[FullTextCandidate]]:
        response = await self.client.get(f"https://api.crossref.org/works/{normalize_doi(doi)}")
        if response.status_code == 404:
            return None, []
        response.raise_for_status()
        item = response.json().get("message", {})
        dates = item.get("published-print") or item.get("published-online") or {}
        year = ((dates.get("date-parts") or [[None]])[0] or [None])[0]
        candidates = []
        for link in item.get("link") or []:
            content_type = link.get("content-type", "")
            fmt = "pdf" if "pdf" in content_type else "xml" if "xml" in content_type else "html"
            if link.get("URL"):
                candidates.append(FullTextCandidate(
                    source="crossref", format=fmt, priority=60, url=link["URL"],
                    access_type="unknown",
                ))
        raw = RawPaperResult(
            source="crossref",
            title=(item.get("title") or [normalize_doi(doi)])[0],
            abstract=item.get("abstract"),
            year=year,
            journal=(item.get("container-title") or [None])[0],
            authors=[
                " ".join(filter(None, [a.get("given"), a.get("family")]))
                for a in item.get("author") or []
            ],
            doi=normalize_doi(doi),
            full_text_candidates=candidates,
        )
        return raw, candidates
