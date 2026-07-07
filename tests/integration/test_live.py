import os

import httpx
import pytest

from app.core.config import Settings
from app.models.requests import ScholarFetchRequest, ScholarSearchRequest
from app.providers.crossref import CrossrefProvider
from app.providers.europepmc import EuropePMCProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.pubmed import PubMedProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.providers.unpaywall import UnpaywallProvider
from app.services.resolver import MetadataResolver
from app.services.scholar_fetch import ScholarFetchService
from app.services.scholar_search import ScholarSearchService

pytestmark = pytest.mark.live


@pytest.fixture
async def services():
    settings = Settings(cache_dir=".cache/live-tests")
    client = httpx.AsyncClient(timeout=30, follow_redirects=True)
    epmc = EuropePMCProvider(client, settings)
    crossref = CrossrefProvider(client, settings)
    search = ScholarSearchService({
        "openalex": OpenAlexProvider(client, settings),
        "europepmc": epmc,
        "semantic_scholar": SemanticScholarProvider(client, settings),
        "pubmed": PubMedProvider(client, settings),
    })
    fetch = ScholarFetchService(
        client, settings, epmc, UnpaywallProvider(client, settings),
        crossref, MetadataResolver(epmc, crossref)
    )
    yield search, fetch
    await client.aclose()


@pytest.mark.asyncio
async def test_live_search(services):
    search, _ = services
    result = await search.search(ScholarSearchRequest(
        query="KRAS G12C sotorasib", max_results=10
    ))
    assert result.total >= 5
    assert any(p.doi for p in result.papers)


@pytest.mark.asyncio
async def test_live_fetch_pmc(services):
    _, fetch = services
    result = await fetch.fetch(ScholarFetchRequest(pmcid="PMC9715446", max_chars=100_000))
    assert result.full_text_status == "success"
    assert "Abstract" in result.content
    assert len(result.content) > 5000
    assert result.retrieval.source in {"europepmc", "pmc"}


@pytest.mark.asyncio
async def test_live_closed_doi_does_not_invent_text(services):
    _, fetch = services
    result = await fetch.fetch(ScholarFetchRequest(doi="10.1007/978-1-4612-4380-9_1"))
    assert result.full_text_status in {"abstract_only", "full_text_unavailable"}
    if result.full_text_status == "abstract_only":
        assert result.content.endswith(result.paper.abstract or "")


@pytest.mark.asyncio
async def test_live_pipeline_components(services):
    search, fetch = services
    result = await search.search(ScholarSearchRequest(
        query="DrugCLIP virtual screening", max_results=5
    ))
    assert result.total > 0
    attempts = []
    for paper in result.papers[:2]:
        try:
            attempts.append(await fetch.fetch(ScholarFetchRequest(
                doi=paper.doi, pmid=paper.pmid, pmcid=paper.pmcid,
                title=paper.title, abstract=paper.abstract
            )))
        except Exception as exc:
            attempts.append(exc)
    assert len(attempts) == min(2, result.total)
