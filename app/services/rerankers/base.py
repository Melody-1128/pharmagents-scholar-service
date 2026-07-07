from typing import Protocol

from app.models.paper import PaperMetadata


class Reranker(Protocol):
    async def rerank(
        self, query: str, papers: list[PaperMetadata]
    ) -> list[PaperMetadata]:
        ...


class NoopReranker:
    async def rerank(
        self, query: str, papers: list[PaperMetadata]
    ) -> list[PaperMetadata]:
        return papers
