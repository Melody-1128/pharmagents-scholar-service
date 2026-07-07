import asyncio
import logging

import httpx

from app.core.config import Settings
from app.models.paper import PaperMetadata
from app.models.requests import ScholarSearchRequest
from app.models.responses import PaperSearchResponse
from app.providers.base import SearchProvider
from app.services.dedup import deduplicate
from app.services.ranking import rank_papers
from app.services.rerankers.base import Reranker
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

PREPRINT_SOURCES = {"biorxiv", "medrxiv", "arxiv"}


class ScholarSearchService:
    def __init__(
        self,
        providers: dict[str, SearchProvider],
        cache: Cache | None = None,
        settings: Settings | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.providers = providers
        self.cache = cache
        self.settings = settings
        self.reranker = reranker

    async def search(self, request: ScholarSearchRequest) -> PaperSearchResponse:
        debug_metadata = not self.settings or self.settings.app_env.lower() == "dev"
        key = Cache.key({
            "schema_version": 6,
            "debug_metadata": debug_metadata,
            "reranker_type": self.settings.reranker_type if self.settings else None,
            "source_top_k_per_provider": (
                self.settings.source_top_k_per_provider if self.settings else None
            ),
            "preprint_source_top_k_per_provider": (
                self.settings.preprint_source_top_k_per_provider if self.settings else None
            ),
            "max_candidates_after_dedup": (
                self.settings.max_candidates_after_dedup if self.settings else None
            ),
            "qwen_rerank_top_k": (
                self.settings.qwen_rerank_top_k if self.settings else None
            ),
            "qwen_rerank_model": self.settings.qwen_rerank_model if self.settings else None,
            "qwen_rerank_path": self.settings.qwen_rerank_path if self.settings else None,
            **request.model_dump(),
        })
        if self.cache and (cached := self.cache.get("search", key)):
            return PaperSearchResponse.model_validate(cached)
        selected = [(name, self.providers[name]) for name in request.sources if name in self.providers]

        def provider_request_for(source_name: str) -> ScholarSearchRequest:
            if self.settings:
                max_results = (
                    self.settings.preprint_source_top_k_per_provider
                    if source_name in PREPRINT_SOURCES
                    else self.settings.source_top_k_per_provider
                )
            else:
                max_results = 20 if source_name in PREPRINT_SOURCES else max(request.max_results, 30)
            return request.model_copy(update={"max_results": max_results})

        results = await asyncio.gather(
            *(provider.search(provider_request_for(name)) for name, provider in selected),
            return_exceptions=True,
        )
        papers, warnings = [], []
        for (name, _), result in zip(selected, results):
            if isinstance(result, Exception):
                logger.warning("Provider %s failed: %s", name, result)
                if isinstance(result, httpx.TimeoutException):
                    warnings.append(f"{name}: provider timeout")
                else:
                    warnings.append(f"{name}: {type(result).__name__}: {result}")
                continue
            papers.extend(raw.normalize() for raw in result)
        candidates = self._candidate_pool(request, deduplicate(papers))
        if self._use_qwen_reranker():
            rerank_input = self._light_prefilter_for_qwen(candidates)
            try:
                ranked = await self.reranker.rerank(request.query, rerank_input) if self.reranker else rerank_input
            except Exception as exc:
                logger.warning("Qwen reranker failed: %s", exc)
                warnings.append(f"Qwen reranker failed: {type(exc).__name__}: {exc}")
                ranked = rank_papers(request.query, candidates)
        else:
            ranked = rank_papers(request.query, candidates)
        if not debug_metadata:
            for paper in ranked:
                paper.field_sources = None
                paper.metadata_conflicts = None
        if request.require_full_text:
            ranked = [paper for paper in ranked if paper.has_full_text]
        response = PaperSearchResponse(
            query=request.query,
            total=min(len(ranked), request.max_results),
            papers=[
                paper.model_dump()
                for paper in ranked[: request.max_results]
            ],
            warnings=warnings,
        )
        if self.cache:
            self.cache.set("search", key, response.model_dump(mode="json"), ttl=3600)
        return response

    def _use_qwen_reranker(self) -> bool:
        return bool(
            self.settings
            and self.settings.reranker_type.lower() == "qwen"
            and self.reranker
        )

    def _candidate_pool(
        self, request: ScholarSearchRequest, papers: list[PaperMetadata]
    ) -> list[PaperMetadata]:
        candidates = [
            paper for paper in papers
            if self._is_basic_eligible(request, paper)
        ]
        limit = (
            self.settings.max_candidates_after_dedup
            if self.settings
            else 200
        )
        return candidates[:limit]

    def _light_prefilter_for_qwen(
        self, candidates: list[PaperMetadata]
    ) -> list[PaperMetadata]:
        limit = self.settings.qwen_rerank_top_k if self.settings else 200
        if len(candidates) <= limit:
            return candidates
        return candidates[:limit]

    @staticmethod
    def _is_basic_eligible(
        request: ScholarSearchRequest, paper: PaperMetadata
    ) -> bool:
        title = (paper.title or "").strip()
        abstract = (paper.abstract or "").strip()
        if not title:
            return False
        if len(title) < 5:
            return False
        if len(title) + len(abstract) < 20:
            return False
        if request.from_year and paper.year and paper.year < request.from_year:
            return False
        if request.to_year and paper.year and paper.year > request.to_year:
            return False
        return True
