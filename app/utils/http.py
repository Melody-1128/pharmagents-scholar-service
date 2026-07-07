import httpx

from app.core.config import Settings


def make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": f"scholar-service/0.1 ({settings.ncbi_email})"},
    )
