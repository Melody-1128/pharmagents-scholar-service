import httpx
import pytest
import respx

from app.core.config import Settings
from app.models.requests import ScholarSearchRequest
from app.providers.europepmc import EuropePMCProvider
from app.providers.openalex import OpenAlexProvider
from app.utils.text import inverted_index_to_text


def test_openalex_inverted_index():
    assert inverted_index_to_text({"world": [1], "hello": [0, 2]}) == "hello world hello"


@pytest.mark.asyncio
@respx.mock
async def test_openalex_provider_normalizes_response():
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(200, json={
        "results": [{
            "id": "https://openalex.org/W1",
            "title": "KRAS paper",
            "publication_year": 2024,
            "abstract_inverted_index": {"An": [0], "abstract": [1]},
            "ids": {"doi": "https://doi.org/10.1000/Test"},
            "open_access": {"is_oa": True},
            "primary_location": {"source": {"display_name": "Nature"}},
            "authorships": [{"author": {"display_name": "A. Author"}}],
            "cited_by_count": 12,
            "best_oa_location": {"pdf_url": "https://example.org/a.pdf", "license": "cc-by"},
        }]
    }))
    async with httpx.AsyncClient() as client:
        result = await OpenAlexProvider(client, Settings()).search(
            ScholarSearchRequest(query="KRAS", sources=["openalex"])
        )
    assert result[0].doi == "10.1000/test"
    assert result[0].abstract == "An abstract"
    assert result[0].full_text_candidates[0].format == "pdf"


@pytest.mark.asyncio
@respx.mock
async def test_europepmc_provider_normalizes_response():
    respx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(200, json={"resultList": {"result": [{
            "title": "Paper", "abstractText": "Abstract", "pubYear": "2023",
            "doi": "10.1/X", "pmid": "1", "pmcid": "PMC2",
            "isOpenAccess": "Y", "authorList": {"author": [{"fullName": "Jane Doe"}]}
        }]}})
    )
    async with httpx.AsyncClient() as client:
        result = await EuropePMCProvider(client, Settings()).search(
            ScholarSearchRequest(query="Paper", sources=["europepmc"])
        )
    assert result[0].pmcid == "PMC2"
    assert result[0].authors == ["Jane Doe"]
    assert result[0].full_text_candidates[0].priority == 1
