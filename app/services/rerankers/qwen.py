import httpx

from app.core.config import Settings
from app.models.paper import PaperMetadata


class QwenReranker:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def rerank(
        self, query: str, papers: list[PaperMetadata]
    ) -> list[PaperMetadata]:
        if self.settings.reranker_type.lower() == "none" or len(papers) <= 1:
            return papers
        if self.settings.reranker_type.lower() != "qwen":
            return papers
        if not self.settings.qwen_api_key:
            raise ValueError("Qwen reranker failed: missing QWEN_API_KEY")

        top_k = min(self.settings.qwen_rerank_top_k, len(papers))
        candidates = papers[:top_k]
        documents = [paper_to_document(paper) for paper in candidates]
        payload = {
            "model": self.settings.qwen_rerank_model,
            "documents": documents,
            "query": query,
            "top_n": top_k,
            "instruct": self.settings.qwen_rerank_instruct,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.qwen_api_key}",
        }
        response = await self.client.post(
            self._url(),
            json=payload,
            headers=headers,
            timeout=self.settings.rerank_timeout_seconds,
        )
        response.raise_for_status()
        reranked_candidates = apply_rerank_results(candidates, response.json())
        return reranked_candidates + papers[top_k:]

    def _url(self) -> str:
        base_url = self.settings.qwen_rerank_base_url.rstrip("/")
        path = self.settings.qwen_rerank_path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base_url}{path}"


def paper_to_document(paper: PaperMetadata) -> str:
    title = paper.title or ""
    abstract = paper.abstract or ""
    year = paper.year or ""
    journal = paper.journal or ""

    text = f"Title: {title}\n"
    if abstract:
        text += f"Abstract: {abstract}\n"
    if year:
        text += f"Year: {year}\n"
    if journal:
        text += f"Journal: {journal}\n"
    return text.strip()


def apply_rerank_results(
    papers: list[PaperMetadata], response_json: dict
) -> list[PaperMetadata]:
    results = response_json.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("Invalid rerank response: missing results")

    selected: list[PaperMetadata] = []
    used: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Invalid rerank response: result item is not an object")
        idx = item.get("index")
        if not isinstance(idx, int):
            raise ValueError("Invalid rerank response: result index is not an integer")
        if idx < 0 or idx >= len(papers):
            raise ValueError("Invalid rerank response: result index out of range")
        if idx in used:
            continue
        selected.append(papers[idx])
        used.add(idx)

    if not selected:
        raise ValueError("Invalid rerank response: no usable results")
    for idx, paper in enumerate(papers):
        if idx not in used:
            selected.append(paper)
    return selected
