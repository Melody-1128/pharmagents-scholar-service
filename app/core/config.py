from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    http_timeout_seconds: float = 20
    max_concurrent_requests: int = 8
    cache_dir: Path = Path(".cache/scholar")
    ncbi_email: str = "scholar-service@example.com"
    unpaywall_email: str = ""
    semantic_scholar_api_key: str = ""
    openalex_api_key: str = ""
    enable_openalex_content: bool = False
    enable_pdf_fetch: bool = True
    enable_grobid: bool = False
    grobid_url: str = "http://localhost:8070"
    max_download_bytes: int = Field(default=25_000_000, ge=1_000_000)
    source_top_k_per_provider: int = Field(default=30, ge=1, le=200)
    preprint_source_top_k_per_provider: int = Field(default=20, ge=1, le=200)
    max_candidates_after_dedup: int = Field(default=200, ge=1, le=1000)
    reranker_type: str = "none"
    qwen_rerank_top_k: int = Field(default=200, ge=1, le=200)
    rerank_top_k: int = Field(default=50, ge=1, le=200)
    rerank_timeout_seconds: float = Field(default=20, ge=1, le=120)
    qwen_api_key: str = ""
    qwen_rerank_base_url: str = "https://api.qingyuntop.top"
    qwen_rerank_path: str = "/v1/rerank"
    qwen_rerank_model: str = "qwen3-rerank"
    qwen_rerank_instruct: str = (
        "Given a web search query, retrieve relevant passages that answer the query."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
