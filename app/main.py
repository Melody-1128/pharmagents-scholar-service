from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.providers.crossref import CrossrefProvider
from app.providers.arxiv import ArxivProvider
from app.providers.biorxiv import BioRxivProvider
from app.providers.europepmc import EuropePMCProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.medrxiv import MedRxivProvider
from app.providers.pubmed import PubMedProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.providers.unpaywall import UnpaywallProvider
from app.services.resolver import MetadataResolver
from app.services.rerankers.qwen import QwenReranker
from app.services.scholar_fetch import ScholarFetchService
from app.services.scholar_search import ScholarSearchService
from app.storage.cache import Cache
from app.utils.http import make_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    client = make_client(settings)
    cache = Cache(settings.cache_dir)
    europepmc = EuropePMCProvider(client, settings)
    crossref = CrossrefProvider(client, settings)
    preprint_providers = {
        "biorxiv": BioRxivProvider(client, settings),
        "medrxiv": MedRxivProvider(client, settings),
        "arxiv": ArxivProvider(client, settings),
    }
    providers = {
        "openalex": OpenAlexProvider(client, settings),
        "europepmc": europepmc,
        "semantic_scholar": SemanticScholarProvider(client, settings),
        "pubmed": PubMedProvider(client, settings),
        **preprint_providers,
    }
    app.state.search_service = ScholarSearchService(
        providers, cache, settings, QwenReranker(client, settings)
    )
    app.state.fetch_service = ScholarFetchService(
        client=client,
        settings=settings,
        europepmc=europepmc,
        unpaywall=UnpaywallProvider(client, settings),
        crossref=crossref,
        resolver=MetadataResolver(europepmc, crossref, preprint_providers),
        cache=cache,
    )
    yield
    await client.aclose()


app = FastAPI(title="Scholar Service", version="0.1.0", lifespan=lifespan)
app.include_router(router)
