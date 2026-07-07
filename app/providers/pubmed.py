from xml.etree import ElementTree as ET

from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult, SearchProvider
from app.utils.ids import normalize_doi, normalize_pmcid


class PubMedProvider(SearchProvider):
    name = "pubmed"
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    async def search(self, request: ScholarSearchRequest) -> list[RawPaperResult]:
        term = request.query
        if request.from_year or request.to_year:
            term += f" AND ({request.from_year or 1800}:{request.to_year or 3000}[pdat])"
        common = {"tool": "scholar-service", "email": self.settings.ncbi_email}
        search = await self.client.get(
            f"{self.base_url}/esearch.fcgi",
            params={**common, "db": "pubmed", "term": term, "retmode": "json", "retmax": request.max_results},
        )
        search.raise_for_status()
        ids = search.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        fetch = await self.client.get(
            f"{self.base_url}/efetch.fcgi",
            params={**common, "db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
        )
        fetch.raise_for_status()
        return self._parse_xml(fetch.content)

    def _parse_xml(self, content: bytes) -> list[RawPaperResult]:
        root = ET.fromstring(content)
        results = []
        for article in root.findall(".//PubmedArticle"):
            citation = article.find("MedlineCitation")
            node = citation.find("Article") if citation is not None else None
            title_node = node.find("ArticleTitle") if node is not None else None
            title = "".join(title_node.itertext()) if title_node is not None else ""
            if not title:
                continue
            ids = {
                x.attrib.get("IdType"): (x.text or "")
                for x in article.findall(".//ArticleId")
            }
            abstract = " ".join(
                "".join(x.itertext()) for x in article.findall(".//Abstract/AbstractText")
            ) or None
            year_text = (
                article.findtext(".//PubDate/Year")
                or article.findtext(".//ArticleDate/Year")
                or ""
            )
            authors = []
            for author in article.findall(".//Author"):
                name = " ".join(filter(None, [author.findtext("ForeName"), author.findtext("LastName")]))
                if name:
                    authors.append(name)
            results.append(RawPaperResult(
                source=self.name,
                title=title,
                abstract=abstract,
                year=int(year_text) if year_text.isdigit() else None,
                journal=article.findtext(".//Journal/Title"),
                authors=authors,
                doi=normalize_doi(ids.get("doi")),
                pmid=ids.get("pubmed") or citation.findtext("PMID"),
                pmcid=normalize_pmcid(ids.get("pmc")),
                is_open_access=bool(ids.get("pmc")),
            ))
        return results
