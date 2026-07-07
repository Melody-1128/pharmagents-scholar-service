import json

import httpx
import pytest
import respx

from app.core.config import Settings
from app.models.paper import PaperMetadata
from app.models.requests import ScholarSearchRequest
from app.providers.base import RawPaperResult
from app.services.rerankers.qwen import (
    QwenReranker,
    apply_rerank_results,
    paper_to_document,
)
from app.services.scholar_search import ScholarSearchService


def papers():
    return [
        PaperMetadata(
            title="KRAS pathway overview",
            abstract="General KRAS biology.",
            doi="10.1/first",
            year=2020,
            journal="Cancer Reviews",
        ),
        PaperMetadata(
            title="KRAS G12C targeted therapy trial",
            abstract="Sotorasib clinical trial in KRAS G12C NSCLC.",
            doi="10.1/second",
            year=2024,
            journal="Oncology",
        ),
        PaperMetadata(
            title="Unrelated quantum paper",
            abstract=None,
            doi="10.1/third",
        ),
    ]


class MultiProvider:
    async def search(self, request):
        return [
            RawPaperResult(
                source="openalex",
                title=paper.title,
                abstract=paper.abstract,
                doi=paper.doi,
                year=paper.year,
                journal=paper.journal,
            )
            for paper in papers()
        ]


def settings(**overrides):
    values = {
        "reranker_type": "qwen",
        "qwen_rerank_top_k": 2,
        "qwen_api_key": "test-key",
        "qwen_rerank_base_url": "https://qwen.example",
        "qwen_rerank_path": "/v1/rerank",
        "qwen_rerank_model": "qwen3-rerank",
        "qwen_rerank_instruct": (
            "Given a web search query, retrieve relevant passages that answer the query."
        ),
    }
    values.update(overrides)
    return Settings(**values)


def test_paper_to_document_uses_title_abstract_and_small_metadata_only():
    document = paper_to_document(papers()[0])
    assert "Title: KRAS pathway overview" in document
    assert "Abstract: General KRAS biology." in document
    assert "Year: 2020" in document
    assert "Journal: Cancer Reviews" in document
    assert "Full text" not in document


def test_apply_rerank_results_uses_returned_order_and_appends_missing():
    original = papers()
    reranked = apply_rerank_results(original, {
        "results": [
            {"index": 2, "document": {"text": "..."}, "relevance_score": 0.96},
            {"index": 0, "relevance_score": 0.91},
        ]
    })
    assert [paper.doi for paper in reranked] == [
        "10.1/third", "10.1/first", "10.1/second",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": "not-a-list"},
        {"results": [{"index": 99, "relevance_score": 1.0}]},
        {"results": [{"index": "0", "relevance_score": 1.0}]},
    ],
)
def test_apply_rerank_results_rejects_invalid_response(payload):
    with pytest.raises(ValueError):
        apply_rerank_results(papers(), payload)


@pytest.mark.asyncio
@respx.mock
async def test_qwen_reranker_calls_actual_rerank_endpoint_and_payload():
    route = respx.post("https://qwen.example/v1/rerank").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"index": 1, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.01},
            ]
        })
    )
    async with httpx.AsyncClient() as client:
        reranked = await QwenReranker(client, settings()).rerank(
            "KRAS G12C therapy", papers()
        )

    assert [paper.doi for paper in reranked] == [
        "10.1/second", "10.1/first", "10.1/third",
    ]
    request = route.calls.last.request
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content.decode())
    assert body["model"] == "qwen3-rerank"
    assert body["query"] == "KRAS G12C therapy"
    assert body["top_n"] == 2
    assert body["instruct"].startswith("Given a web search query")
    assert len(body["documents"]) == 2
    assert body["documents"][0].startswith("Title:")
    assert "Abstract:" in body["documents"][0]


@pytest.mark.asyncio
@respx.mock
async def test_search_qwen_reranker_failure_falls_back_to_original_order():
    respx.post("https://qwen.example/v1/rerank").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    async with httpx.AsyncClient() as client:
        service = ScholarSearchService(
            {"openalex": MultiProvider()},
            settings=settings(),
            reranker=QwenReranker(client, settings()),
        )
        response = await service.search(
            ScholarSearchRequest(query="KRAS G12C therapy", sources=["openalex"])
        )
    assert response.total == 3
    assert response.warnings
    assert response.warnings[0].startswith("Qwen reranker failed")
    assert "test-key" not in response.warnings[0]


@pytest.mark.asyncio
@respx.mock
async def test_search_qwen_success_does_not_call_local_ranking(monkeypatch):
    respx.post("https://qwen.example/v1/rerank").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"index": 1, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.01},
            ]
        })
    )

    def fail_if_local_ranking_is_called(query, papers):
        raise AssertionError("local ranking should not run before successful Qwen rerank")

    monkeypatch.setattr(
        "app.services.scholar_search.rank_papers",
        fail_if_local_ranking_is_called,
    )
    async with httpx.AsyncClient() as client:
        service = ScholarSearchService(
            {"openalex": MultiProvider()},
            settings=settings(qwen_rerank_top_k=2),
            reranker=QwenReranker(client, settings(qwen_rerank_top_k=2)),
        )
        response = await service.search(
            ScholarSearchRequest(query="KRAS G12C therapy", sources=["openalex"])
        )
    assert response.total == 2
    assert response.papers[0].doi != response.papers[1].doi


@pytest.mark.asyncio
async def test_search_does_not_rule_rank_before_qwen():
    class OrderedProvider:
        async def search(self, request):
            return [
                RawPaperResult(
                    source="openalex",
                    title="First provider result",
                    abstract="Short but sufficiently descriptive abstract.",
                    doi="10.1/first",
                    year=2020,
                ),
                RawPaperResult(
                    source="openalex",
                    title="Second provider result with full text and newer year",
                    abstract=(
                        "A much longer abstract with enough text that the old "
                        "light candidate score would have preferred this candidate."
                    ),
                    doi="10.1/second",
                    year=2026,
                    is_open_access=True,
                ),
            ]

    class RecordingReranker:
        def __init__(self):
            self.input_titles = []

        async def rerank(self, query, papers):
            self.input_titles = [paper.title for paper in papers]
            return papers

    reranker = RecordingReranker()
    service = ScholarSearchService(
        {"openalex": OrderedProvider()},
        settings=settings(qwen_rerank_top_k=10),
        reranker=reranker,
    )
    await service.search(
        ScholarSearchRequest(query="ordered provider result", sources=["openalex"])
    )
    assert reranker.input_titles == [
        "First provider result",
        "Second provider result with full text and newer year",
    ]


@pytest.mark.asyncio
async def test_search_provider_recall_uses_source_top_k_per_provider():
    seen_max_results = []

    class RecordingProvider:
        async def search(self, request):
            seen_max_results.append(request.max_results)
            return [
                RawPaperResult(
                    source="openalex",
                    title="A sufficiently descriptive title",
                    abstract="A sufficiently long abstract for eligibility.",
                    doi="10.1/a",
                )
            ]

    service = ScholarSearchService(
        {"openalex": RecordingProvider()},
        settings=Settings(
            reranker_type="none",
            source_top_k_per_provider=37,
        ),
    )
    response = await service.search(
        ScholarSearchRequest(query="KRAS", max_results=3, sources=["openalex"])
    )
    assert seen_max_results == [37]
    assert response.total == 1


@pytest.mark.asyncio
async def test_search_provider_recall_uses_separate_preprint_top_k():
    seen_max_results = {}

    class RecordingProvider:
        def __init__(self, source):
            self.source = source

        async def search(self, request):
            seen_max_results[self.source] = request.max_results
            return [
                RawPaperResult(
                    source=self.source,
                    title=f"{self.source} sufficiently descriptive title",
                    abstract="A sufficiently long abstract for eligibility.",
                    doi=f"10.1/{self.source}",
                    is_preprint=self.source in {"biorxiv", "medrxiv", "arxiv"},
                )
            ]

    service = ScholarSearchService(
        {
            "openalex": RecordingProvider("openalex"),
            "biorxiv": RecordingProvider("biorxiv"),
            "medrxiv": RecordingProvider("medrxiv"),
            "arxiv": RecordingProvider("arxiv"),
        },
        settings=Settings(
            reranker_type="none",
            source_top_k_per_provider=30,
            preprint_source_top_k_per_provider=20,
        ),
    )
    response = await service.search(
        ScholarSearchRequest(
            query="protein foundation model",
            max_results=4,
            sources=["openalex", "biorxiv", "medrxiv", "arxiv"],
        )
    )
    assert seen_max_results == {
        "openalex": 30,
        "biorxiv": 20,
        "medrxiv": 20,
        "arxiv": 20,
    }
    assert response.total == 4


@pytest.mark.asyncio
@respx.mock
async def test_search_qwen_reranker_missing_api_key_warns_and_falls_back():
    route = respx.post("https://qwen.example/v1/rerank").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    no_key_settings = settings(qwen_api_key="")
    async with httpx.AsyncClient() as client:
        service = ScholarSearchService(
            {"openalex": MultiProvider()},
            settings=no_key_settings,
            reranker=QwenReranker(client, no_key_settings),
        )
        response = await service.search(
            ScholarSearchRequest(query="KRAS G12C therapy", sources=["openalex"])
        )
    assert response.total == 3
    assert response.warnings[0].startswith("Qwen reranker failed")
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_reranker_type_none_does_not_call_qwen():
    route = respx.post("https://qwen.example/v1/rerank").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    async with httpx.AsyncClient() as client:
        result = await QwenReranker(
            client, settings(reranker_type="none")
        ).rerank("query", papers())
    assert [paper.doi for paper in result] == [
        "10.1/first", "10.1/second", "10.1/third",
    ]
    assert not route.called
