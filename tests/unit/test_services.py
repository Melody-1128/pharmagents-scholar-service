import httpx
import pytest
import respx

from app.core.config import Settings
from app.models.fulltext import FullTextSection
from app.models.paper import FullTextCandidate, PaperMetadata
from app.models.requests import ScholarFetchRequest, ScholarSearchRequest
from app.providers.crossref import CrossrefProvider
from app.providers.base import RawPaperResult
from app.providers.europepmc import EuropePMCProvider
from app.providers.unpaywall import UnpaywallProvider
from app.services.resolver import MetadataResolver
from app.services.scholar_fetch import ScholarFetchService
from app.services.scholar_search import ScholarSearchService


class GoodProvider:
    async def search(self, request):
        from app.providers.base import RawPaperResult
        return [RawPaperResult(
            source="good",
            title="KRAS G12C resistance mechanisms",
            abstract="A sufficiently descriptive abstract for search eligibility.",
            doi="10.1/a",
        )]


class BadProvider:
    async def search(self, request):
        raise httpx.ReadTimeout("slow")


class MultiProvider:
    async def search(self, request):
        return [
            RawPaperResult(
                source="openalex",
                title="KRAS pathway overview",
                abstract="General KRAS biology.",
                doi="10.1/first",
            ),
            RawPaperResult(
                source="openalex",
                title="KRAS G12C targeted therapy trial",
                abstract="Sotorasib clinical trial in KRAS G12C NSCLC.",
                doi="10.1/second",
            ),
        ]


@pytest.mark.asyncio
async def test_search_survives_provider_failure():
    service = ScholarSearchService({"openalex": GoodProvider(), "pubmed": BadProvider()})
    response = await service.search(
        ScholarSearchRequest(query="KRAS", sources=["openalex", "pubmed"])
    )
    assert response.total == 1
    assert response.warnings
    assert response.papers[0].doi == "10.1/a"
    assert "field_sources" not in response.papers[0].model_dump()
    assert "source_hits" not in response.papers[0].model_dump()
    assert "score" not in response.papers[0].model_dump()


@pytest.mark.asyncio
async def test_search_hides_provenance_outside_dev():
    service = ScholarSearchService(
        {"openalex": GoodProvider()},
        settings=Settings(app_env="prod"),
    )
    response = await service.search(
        ScholarSearchRequest(query="KRAS", sources=["openalex"])
    )
    dumped = response.papers[0].model_dump()
    assert "field_sources" not in dumped
    assert "metadata_conflicts" not in dumped
    assert "query_relevance" not in dumped
    assert "biomedical_score" not in dumped


class StubResolver:
    async def resolve(self, request):
        return PaperMetadata(
            paper_id="pmcid:PMC1", title="Paper", pmcid="PMC1", abstract="Fallback abstract"
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_prefers_europepmc_xml():
    xml = (
        b"<article><front><article-meta><article-title>Paper</article-title>"
        b"<abstract><p>Abstract</p></abstract></article-meta></front>"
        b"<body><sec><title>Results</title><p>Body text with enough substantive "
        b"content to confirm that a full article section was parsed successfully."
        b"</p></sec></body></article>"
    )
    respx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML").mock(
        return_value=httpx.Response(200, content=xml)
    )
    settings = Settings()
    async with httpx.AsyncClient() as client:
        epmc = EuropePMCProvider(client, settings)
        service = ScholarFetchService(
            client, settings, epmc, UnpaywallProvider(client, settings),
            CrossrefProvider(client, settings), StubResolver()
        )
        response = await service.fetch(ScholarFetchRequest(pmcid="PMC1"))
    assert response.full_text_status == "success"
    assert response.retrieval.source == "europepmc"
    assert response.retrieval.format == "xml"
    assert response.paper.has_full_text is True
    assert response.paper.is_open_access is True
    assert "Abstract\nAbstract" in response.content
    assert "Results\nBody text" in response.content
    dumped = response.model_dump()
    assert "content" in dumped
    assert "sections" not in dumped
    assert "plain_text" not in dumped


class PmcidOnlyResolver:
    async def resolve(self, request):
        return PaperMetadata(
            paper_id="pmcid:PMC9715446",
            title="PMC9715446",
            pmcid="PMC9715446",
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_pmcid_enriches_real_metadata_from_xml():
    xml = b"""
    <article article-type="research-article">
      <front>
        <journal-meta><journal-title>Clinical Journal</journal-title></journal-meta>
        <article-meta>
          <article-id pub-id-type="doi">10.1000/real-doi</article-id>
          <article-id pub-id-type="pmid">36543210</article-id>
          <article-id pub-id-type="pmc">PMC9715446</article-id>
          <title-group><article-title>Real fetched article title</article-title></title-group>
          <contrib-group><contrib contrib-type="author"><name>
            <surname>Doe</surname><given-names>Jane</given-names>
          </name></contrib></contrib-group>
          <pub-date pub-type="epub"><year>2022</year><month>12</month></pub-date>
          <abstract><p>Real fetched abstract.</p></abstract>
        </article-meta>
      </front>
      <body><sec><title>Results</title><p>The article body contains enough
      substantive text to qualify as successfully parsed full text content.</p>
      </sec></body>
    </article>
    """
    respx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(500)
    )
    respx.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9715446/fullTextXML"
    ).mock(return_value=httpx.Response(200, content=xml))
    settings = Settings(app_env="dev")
    async with httpx.AsyncClient() as client:
        epmc = EuropePMCProvider(client, settings)
        service = ScholarFetchService(
            client, settings, epmc, UnpaywallProvider(client, settings),
            CrossrefProvider(client, settings), PmcidOnlyResolver()
        )
        response = await service.fetch(ScholarFetchRequest(pmcid="PMC9715446"))
    assert response.full_text_status == "success"
    assert response.paper.title == "Real fetched article title"
    assert response.paper.doi == "10.1000/real-doi"
    assert response.paper.paper_id == "doi:10.1000/real-doi"
    assert response.paper.pmid == "36543210"
    assert response.paper.pmcid == "PMC9715446"
    assert response.paper.year == 2022
    assert response.paper.journal == "Clinical Journal"
    assert response.paper.authors == ["Jane Doe"]
    assert response.paper.has_full_text is True
    assert response.paper.is_open_access is True
    assert response.paper.is_preprint is False
    assert response.paper.review_status == "published"
    assert response.paper.field_sources["title"] == ["europepmc_xml"]
    assert response.paper.field_sources["has_full_text"] == ["europepmc"]


class BatchResolver:
    async def resolve(self, request):
        if request.pmcid == "PMC1":
            return PaperMetadata(
                paper_id="pmcid:PMC1",
                title="Paper with XML",
                pmcid="PMC1",
                abstract="XML abstract",
            )
        if request.doi == "10.1/abstract":
            return PaperMetadata(
                paper_id="doi:10.1/abstract",
                title="Abstract only",
                doi="10.1/abstract",
                abstract="Only metadata is available.",
            )
        raise RuntimeError("resolver exploded")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_batch_isolates_failures_and_returns_content():
    xml = (
        b"<article><front><article-meta><article-title>Paper with XML</article-title>"
        b"<abstract><p>XML abstract</p></abstract></article-meta></front>"
        b"<body><sec><title>Introduction</title><p>Full body text with enough "
        b"substantive content to be treated as successful full text.</p>"
        b"</sec></body></article>"
    )
    respx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML").mock(
        return_value=httpx.Response(200, content=xml)
    )
    settings = Settings()
    async with httpx.AsyncClient() as client:
        service = ScholarFetchService(
            client, settings, EuropePMCProvider(client, settings),
            NoopUnpaywall(), NoopCrossref(), BatchResolver()
        )
        response = await service.fetch_batch(ScholarFetchRequest(
            papers=[
                {"pmcid": "PMC1"},
                {"doi": "10.1/abstract"},
                {"title": "will fail"},
            ],
            max_chars_per_paper=50_000,
        ))
    assert response.total == 3
    assert response.succeeded == 2
    assert response.failed == 1
    assert response.results[0].full_text_status == "success"
    assert "Introduction\nFull body text" in response.results[0].content
    assert response.results[1].full_text_status == "abstract_only"
    assert response.results[1].content == "Abstract\nOnly metadata is available."
    assert response.results[2].full_text_status == "failed"
    for item in response.results:
        dumped = item.model_dump()
        assert "content" in dumped
        assert "sections" not in dumped
        assert "plain_text" not in dumped


@pytest.mark.asyncio
@respx.mock
async def test_fetch_skips_unverified_crossref_link():
    doi = "10.1/closed"
    respx.get(f"https://api.crossref.org/works/{doi}").mock(
        return_value=httpx.Response(200, json={"message": {
            "title": ["Closed paper"],
            "abstract": "Metadata abstract",
            "DOI": doi,
            "link": [{"URL": "https://publisher.example/full.pdf", "content-type": "application/pdf"}],
        }})
    )
    settings = Settings(unpaywall_email="")
    async with httpx.AsyncClient() as client:
        epmc = EuropePMCProvider(client, settings)
        crossref = CrossrefProvider(client, settings)
        service = ScholarFetchService(
            client, settings, epmc, UnpaywallProvider(client, settings),
            crossref, MetadataResolver(epmc, crossref)
        )
        response = await service.fetch(ScholarFetchRequest(doi=doi))
    assert response.full_text_status == "abstract_only"
    assert response.content == "Abstract\nMetadata abstract"
    assert any("open access was not verified" in warning for warning in response.warnings)


class StubPreprintResolver:
    async def resolve(self, request):
        return PaperMetadata(
            paper_id="arxiv:2601.01234",
            title="Preprint",
            abstract="A real preprint abstract.",
            arxiv_id="2601.01234",
            server="arxiv",
            landing_url="https://arxiv.org/abs/2601.01234",
            pdf_url="https://arxiv.org/pdf/2601.01234",
            is_preprint=True,
            peer_reviewed=False,
            review_status="preprint",
            is_open_access=True,
            has_full_text=True,
            source_hits=["arxiv"],
            full_text_candidates=[FullTextCandidate(
                source="arxiv", format="pdf", priority=45,
                url="https://arxiv.org/pdf/2601.01234"
            )],
        )


@pytest.mark.asyncio
async def test_preprint_fetch_falls_back_to_abstract_and_links():
    settings = Settings()
    async with httpx.AsyncClient() as client:
        epmc = EuropePMCProvider(client, settings)
        service = ScholarFetchService(
            client, settings, epmc, UnpaywallProvider(client, settings),
            CrossrefProvider(client, settings), StubPreprintResolver()
        )
        response = await service.fetch(ScholarFetchRequest(
            arxiv_id="2601.01234", allow_pdf=False
    ))
    assert response.full_text_status == "abstract_only"
    assert response.content == "Abstract\nA real preprint abstract."
    assert response.paper.pdf_url == "https://arxiv.org/pdf/2601.01234"
    assert response.paper.peer_reviewed is False
    assert response.retrieval.source == "arxiv"
    assert response.retrieval.format == "abstract"


class NoopUnpaywall:
    async def resolve(self, doi):
        return []


class NoopCrossref:
    async def metadata(self, doi):
        return None, []


class RxivResolver:
    def __init__(self, candidates=None):
        self.candidates = candidates or []

    async def resolve(self, request):
        return PaperMetadata(
            paper_id="doi:10.1101/test",
            title="bioRxiv preprint",
            abstract="Preprint abstract.",
            doi="10.1101/test",
            server="biorxiv",
            landing_url="https://www.biorxiv.org/content/10.1101/test",
            pdf_url="https://www.biorxiv.org/content/10.1101/test.full.pdf",
            is_preprint=True,
            peer_reviewed=False,
            review_status="preprint",
            is_open_access=True,
            has_full_text=bool(self.candidates),
            source_hits=["biorxiv"],
            full_text_candidates=self.candidates,
        )


class EuropePMCWithDoiXml:
    async def find_by_doi(self, doi):
        return RawPaperResult(
            source="europepmc", title="Preprint", doi=doi, pmcid="PMC123"
        )

    async def fetch_xml(self, pmcid):
        return (
            b"<article><front><article-meta><article-title>Preprint</article-title>"
            b"<abstract><p>Abstract</p></abstract></article-meta></front>"
            b"<body><sec><title>Results</title><p>Europe PMC body with enough "
            b"substantive full-text content to prove this is more than an abstract."
            b"</p></sec></body>"
            b"</article>",
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML",
        )


@pytest.mark.asyncio
async def test_rxiv_fetch_prefers_europepmc_xml_by_doi():
    settings = Settings()
    async with httpx.AsyncClient() as client:
        service = ScholarFetchService(
            client, settings, EuropePMCWithDoiXml(), NoopUnpaywall(),
            NoopCrossref(), RxivResolver([
                FullTextCandidate(
                    source="biorxiv", format="html", priority=20,
                    url="https://www.biorxiv.org/content/10.1101/test",
                )
            ])
        )
        response = await service.fetch(ScholarFetchRequest(
            doi="10.1101/test", source="biorxiv", is_preprint=True
        ))
    assert response.full_text_status == "success"
    assert response.retrieval.source == "europepmc"
    assert response.retrieval.format == "xml"
    assert response.paper.pmcid == "PMC123"


class EuropePMCNoDoiMatch:
    async def find_by_doi(self, doi):
        return None


@pytest.mark.asyncio
@respx.mock
async def test_rxiv_fetch_falls_back_xml_then_html_before_pdf():
    xml_url = "https://www.biorxiv.org/content/10.1101/test.source.xml"
    html_url = "https://www.biorxiv.org/content/10.1101/test"
    pdf_url = "https://www.biorxiv.org/content/10.1101/test.full.pdf"
    xml_route = respx.get(xml_url).mock(return_value=httpx.Response(404))
    html_route = respx.get(html_url).mock(return_value=httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Preprint</title></head><body><article>"
             "<h2>Results</h2><p>HTML full text with enough substantive content "
             "to prove that this response includes article body text and not only "
             "a metadata page or abstract.</p></article></body></html>",
    ))
    pdf_route = respx.get(pdf_url).mock(return_value=httpx.Response(
        200, content=b"%PDF-not-needed"
    ))
    candidates = [
        FullTextCandidate(source="biorxiv", format="pdf", priority=30, url=pdf_url),
        FullTextCandidate(source="biorxiv", format="html", priority=20, url=html_url),
        FullTextCandidate(source="biorxiv", format="xml", priority=10, url=xml_url),
    ]
    settings = Settings()
    async with httpx.AsyncClient() as client:
        service = ScholarFetchService(
            client, settings, EuropePMCNoDoiMatch(), NoopUnpaywall(),
            NoopCrossref(), RxivResolver(candidates)
        )
        response = await service.fetch(ScholarFetchRequest(
            doi="10.1101/test",
            source="biorxiv",
            is_preprint=True,
            prefer_formats=["pdf", "html", "xml"],
        ))
    assert xml_route.called
    assert html_route.called
    assert not pdf_route.called
    assert response.full_text_status == "success"
    assert response.retrieval.source == "biorxiv"
    assert response.retrieval.format == "html"


@pytest.mark.asyncio
@respx.mock
async def test_rxiv_pdf_fallback_reports_failed_xml_and_html(monkeypatch):
    xml_url = "https://www.biorxiv.org/content/10.64898/test.source.xml"
    html_url = "https://www.biorxiv.org/content/10.64898/test"
    pdf_url = "https://www.biorxiv.org/content/10.64898/test.full.pdf"
    respx.get(xml_url).mock(return_value=httpx.Response(403))
    respx.get(html_url).mock(return_value=httpx.Response(403))
    respx.get(pdf_url).mock(return_value=httpx.Response(
        200, headers={"content-type": "application/pdf"}, content=b"%PDF-test"
    ))
    monkeypatch.setattr(
        "app.services.scholar_fetch.parse_pdf",
        lambda content: ({}, [FullTextSection(
            section_type="results",
            heading="Full text",
            text=(
                "Extracted PDF full text with enough substantive content to "
                "confirm successful fallback parsing from the preprint PDF."
            ),
        )]),
    )
    candidates = [
        FullTextCandidate(source="biorxiv", format="xml", priority=10, url=xml_url),
        FullTextCandidate(source="biorxiv", format="html", priority=20, url=html_url),
        FullTextCandidate(source="biorxiv", format="pdf", priority=30, url=pdf_url),
    ]
    settings = Settings()
    async with httpx.AsyncClient() as client:
        service = ScholarFetchService(
            client, settings, EuropePMCNoDoiMatch(), NoopUnpaywall(),
            NoopCrossref(), RxivResolver(candidates)
        )
        response = await service.fetch(ScholarFetchRequest(
            doi="10.64898/test", source="biorxiv", is_preprint=True
        ))
    assert response.full_text_status == "success"
    assert response.retrieval.source == "biorxiv"
    assert response.retrieval.format == "pdf"
    assert any(
        warning == "XML/HTML failed; biorxiv PDF fallback was used."
        for warning in response.warnings
    )
