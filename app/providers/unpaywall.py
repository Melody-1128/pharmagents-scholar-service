from dataclasses import dataclass, field

from app.models.paper import FullTextCandidate
from app.utils.ids import normalize_doi


@dataclass
class UnpaywallResult:
    """Unpaywall lookup outcome: full-text candidates plus the OA verdict.

    ``is_oa`` is Unpaywall's bibliographic open-access claim; it is kept
    separate from whether the pipeline can actually retrieve the file.
    """

    candidates: list[FullTextCandidate] = field(default_factory=list)
    is_oa: bool | None = None
    oa_status: str | None = None


class UnpaywallProvider:
    def __init__(self, client, settings) -> None:
        self.client = client
        self.settings = settings

    async def resolve(self, doi: str) -> UnpaywallResult:
        if not self.settings.unpaywall_email:
            return UnpaywallResult()
        response = await self.client.get(
            f"https://api.unpaywall.org/v2/{normalize_doi(doi)}",
            params={"email": self.settings.unpaywall_email},
        )
        response.raise_for_status()
        data = response.json()
        locations = []
        best = data.get("best_oa_location")
        if best:
            locations.append(best)
        locations.extend(data.get("oa_locations") or [])
        candidates = []
        seen = set()
        for loc in locations:
            for fmt, key, priority in [("pdf", "url_for_pdf", 30), ("html", "url_for_landing_page", 35)]:
                url = loc.get(key)
                if url and url not in seen:
                    seen.add(url)
                    candidates.append(FullTextCandidate(
                        source="unpaywall", format=fmt, priority=priority,
                        url=url, license=loc.get("license")
                    ))
        return UnpaywallResult(
            candidates=candidates,
            is_oa=data.get("is_oa"),
            oa_status=data.get("oa_status"),
        )
