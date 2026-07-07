from app.models.paper import FullTextCandidate
from app.utils.ids import normalize_doi


class UnpaywallProvider:
    def __init__(self, client, settings) -> None:
        self.client = client
        self.settings = settings

    async def resolve(self, doi: str) -> list[FullTextCandidate]:
        if not self.settings.unpaywall_email:
            return []
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
        return candidates
