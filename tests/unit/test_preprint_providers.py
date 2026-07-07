import httpx
import pytest
import respx

from app.core.config import Settings
from app.models.requests import ScholarSearchRequest
from app.models.requests import ScholarFetchRequest
from app.providers.arxiv import ArxivProvider
from app.providers.biorxiv import BioRxivProvider
from app.providers.medrxiv import MedRxivProvider
from app.utils.ids import normalize_arxiv_id, normalize_pmcid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_class", "server"),
    [(BioRxivProvider, "biorxiv"), (MedRxivProvider, "medrxiv")],
)
@respx.mock
async def test_rxiv_provider_normalizes_official_api(provider_class, server):
    respx.get(f"https://api.biorxiv.org/details/{server}/30d/0").mock(
        return_value=httpx.Response(200, json={"collection": [{
            "doi": "10.1101/2026.01.02.123456",
            "title": "A new AI method for protein design",
            "authors": "Jane Doe; John Smith",
            "date": "2026-01-02",
            "category": "bioinformatics",
            "abstract": "An AI method for efficient protein generation.",
            "license": "cc_by",
            "jatsxml": f"/content/10.1101/2026.01.02.123456.source.xml",
            "published": "10.1000/published-version",
            "server": server,
        }]})
    )
    async with httpx.AsyncClient() as client:
        results = await provider_class(client, Settings()).search(
            ScholarSearchRequest(query="AI protein", sources=[server])
        )
    paper = results[0].normalize()
    assert paper.source_hits == [server]
    assert paper.server == server
    assert paper.is_preprint is True
    assert paper.peer_reviewed is False
    assert paper.review_status == "preprint"
    assert paper.published_doi == "10.1000/published-version"
    assert paper.category == "bioinformatics"
    assert paper.landing_url.endswith("10.1101/2026.01.02.123456")
    assert paper.pdf_url.endswith(".full.pdf")
    assert [candidate.format for candidate in paper.full_text_candidates] == [
        "xml", "html", "pdf"
    ]
    assert paper.full_text_candidates[0].url == (
        f"https://www.{server}.org/content/10.1101/2026.01.02.123456.source.xml"
    )


@pytest.mark.asyncio
@respx.mock
async def test_arxiv_provider_normalizes_atom_feed():
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>https://arxiv.org/abs/2601.01234v2</id>
        <published>2026-01-03T00:00:00Z</published>
        <title>New foundation model for molecules</title>
        <summary>A model for molecular generation.</summary>
        <author><name>Jane Doe</name></author>
        <author><name>John Smith</name></author>
        <arxiv:primary_category term="cs.LG"/>
        <arxiv:doi>10.1000/example</arxiv:doi>
        <link rel="alternate" href="https://arxiv.org/abs/2601.01234v2"/>
        <link title="pdf" href="https://arxiv.org/pdf/2601.01234v2"/>
      </entry>
    </feed>"""
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, content=feed)
    )
    async with httpx.AsyncClient() as client:
        results = await ArxivProvider(client, Settings()).search(
            ScholarSearchRequest(query="foundation model", sources=["arxiv"])
        )
    paper = results[0].normalize()
    assert paper.arxiv_id == "2601.01234v2"
    assert paper.doi == "10.1000/example"
    assert paper.category == "cs.LG"
    assert paper.authors == ["Jane Doe", "John Smith"]
    assert paper.is_preprint and paper.peer_reviewed is False
    assert paper.pdf_url == "https://arxiv.org/pdf/2601.01234v2"


def test_placeholder_identifiers_are_not_valid_routes():
    request = ScholarFetchRequest(
        doi="10.64898/2026.05.09.720556",
        pmcid="STRING",
        arxiv_id="string",
        source="biorxiv",
        is_preprint=True,
    )
    assert request.pmcid is None
    assert request.arxiv_id is None
    assert normalize_pmcid("PMC9715446") == "PMC9715446"
    assert normalize_pmcid("STRING") is None
    assert normalize_arxiv_id("2601.01234v2") == "2601.01234v2"
    assert normalize_arxiv_id("string") is None


@pytest.mark.asyncio
@respx.mock
async def test_invalid_arxiv_id_does_not_call_api():
    async with httpx.AsyncClient() as client:
        result = await ArxivProvider(client, Settings()).fetch_by_id("string")
    assert result is None
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_biorxiv_discovers_real_assets_and_normalizes_double_slashes():
    doi = "10.64898/2026.05.09.720556"
    respx.get(f"https://api.biorxiv.org/details/biorxiv/{doi}/na/json").mock(
        return_value=httpx.Response(200, json={"collection": [{
            "doi": doi,
            "title": "Preprint",
            "authors": "Jane Doe",
            "date": "2026-05-09",
            "category": "bioinformatics",
            "abstract": "Abstract",
            "jatsxml": (
                "https://www.biorxiv.org/content/early/2026/05/13//"
                "2026.05.09.720556.source.xml"
            ),
        }]})
    )
    landing = f"https://www.biorxiv.org/content/{doi}"
    respx.get(landing).mock(return_value=httpx.Response(
        200,
        text=(
            "<html><head>"
            f"<meta name='citation_xml_url' content='{landing}.source.xml'>"
            f"<meta name='citation_pdf_url' content='{landing}.full.pdf'>"
            "</head></html>"
        ),
    ))
    async with httpx.AsyncClient() as client:
        result = await BioRxivProvider(client, Settings()).fetch_by_doi(doi)
    urls = [candidate.url for candidate in result.full_text_candidates]
    assert f"{landing}.source.xml" in urls
    assert all("//2026.05.09" not in url for url in urls)
