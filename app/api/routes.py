import asyncio

from fastapi import APIRouter, Request

from app.models.requests import ScholarFetchRequest, ScholarPipelineRequest, ScholarSearchRequest
from app.models.responses import (
    HealthResponse, PipelineItem, ScholarFetchBatchResponse, ScholarFetchResponse,
    ScholarPipelineResponse, PaperSearchResponse,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.post("/scholar/search", response_model=PaperSearchResponse)
async def search(payload: ScholarSearchRequest, request: Request) -> PaperSearchResponse:
    return await request.app.state.search_service.search(payload)


@router.post(
    "/scholar/fetch",
    response_model=ScholarFetchResponse | ScholarFetchBatchResponse,
)
async def fetch(
    payload: ScholarFetchRequest, request: Request
) -> ScholarFetchResponse | ScholarFetchBatchResponse:
    if payload.papers:
        return await request.app.state.fetch_service.fetch_batch(payload)
    return await request.app.state.fetch_service.fetch(payload)


@router.post("/scholar/pipeline", response_model=ScholarPipelineResponse)
async def pipeline(payload: ScholarPipelineRequest, request: Request) -> ScholarPipelineResponse:
    search_response = await request.app.state.search_service.search(
        ScholarSearchRequest(
            query=payload.query,
            max_results=payload.max_search_results,
            require_full_text=payload.require_full_text,
        )
    )
    papers = search_response.papers[: payload.fetch_top_n]
    results = await asyncio.gather(
        *(
            request.app.state.fetch_service.fetch(ScholarFetchRequest(
                doi=paper.doi,
                pmid=paper.pmid,
                pmcid=paper.pmcid,
                openalex_id=paper.openalex_id,
                semantic_scholar_id=paper.semantic_scholar_id,
                arxiv_id=paper.arxiv_id,
                title=paper.title,
                abstract=paper.abstract,
                source=paper.server,
                category=paper.category,
                landing_url=paper.landing_url,
                pdf_url=paper.pdf_url,
                is_preprint=paper.is_preprint,
            ))
            for paper in papers
        ),
        return_exceptions=True,
    )
    fetched = []
    for paper, result in zip(papers, results):
        if isinstance(result, Exception):
            fetched.append(PipelineItem(paper=paper, error=f"{type(result).__name__}: {result}"))
        else:
            fetched.append(PipelineItem(paper=paper, fetch=result))
    return ScholarPipelineResponse(search=search_response, fetched=fetched)
