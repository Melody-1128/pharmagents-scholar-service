"""Regression tests for Information Source 2.0 Phase 1 (scholar-service side).

Covers:
* Search -> fetch metadata continuity / merge (spec Test A, section 6)
* DOI does not suppress other enrichment paths (spec Test B, section 4)
* abstract_only vs metadata_only invariant (spec section 17)
* has_full_text semantic split (spec section 18)
"""
from unittest.mock import MagicMock

import pytest

from app.models.paper import FullTextCandidate, PaperMetadata
from app.models.requests import ScholarFetchRequest
from app.providers.base import RawPaperResult
from app.services.resolver import MetadataResolver, _merge
from app.services.scholar_fetch import ScholarFetchService


# --------------------------------------------------------------------------
# _merge: metadata continuity (Test A / section 6)
# --------------------------------------------------------------------------
def test_merge_never_nulls_existing_abstract():
    base = PaperMetadata(title="t", abstract="search abstract")
    _merge(base, PaperMetadata(title="t", abstract=None))
    assert base.abstract == "search abstract"


def test_merge_prefers_richer_abstract():
    base = PaperMetadata(title="t", abstract="short")
    _merge(base, PaperMetadata(title="t", abstract="a considerably longer abstract"))
    assert base.abstract == "a considerably longer abstract"


def test_merge_unions_identifiers_and_keeps_existing():
    # search PMID present, Crossref-style record has none -> PMID survives;
    # incoming PMCID fills the gap (union).
    base = PaperMetadata(title="t", pmid="40580478", pmcid=None, doi="10.1/x")
    _merge(base, PaperMetadata(title="t", pmid=None, pmcid="PMC12336999", doi="10.1/x"))
    assert base.pmid == "40580478"
    assert base.pmcid == "PMC12336999"


def test_merge_replaces_placeholder_title():
    base = PaperMetadata(title="Unknown paper")
    _merge(base, PaperMetadata(title="Real Title"))
    assert base.title == "Real Title"


def test_merge_replaces_doi_seeded_title():
    # resolver seeds title from the DOI when no real title is known; a real
    # incoming title must still win over that identifier-as-title placeholder.
    base = PaperMetadata(title="10.1016/j.chom.2024.03.013", doi="10.1016/j.chom.2024.03.013")
    _merge(base, PaperMetadata(title="Burkholderia thailandensis T6SS study"))
    assert base.title == "Burkholderia thailandensis T6SS study"


def test_merge_keeps_real_title_over_incoming():
    base = PaperMetadata(title="An Existing Real Title", doi="10.1/x")
    _merge(base, PaperMetadata(title="Different Title"))
    assert base.title == "An Existing Real Title"


# --------------------------------------------------------------------------
# DOI does not suppress enrichment (Test B / section 4)
# --------------------------------------------------------------------------
class _FakeCrossref:
    """Returns a DOI record with NO pmcid (like most Crossref records)."""

    def __init__(self):
        self.calls = []

    async def metadata(self, doi):
        self.calls.append(doi)
        return RawPaperResult(source="crossref", title="Crossref Title", doi=doi), []


class _FakeEuropePMC:
    """Records whether it was queried; returns the PMCID for the paper."""

    def __init__(self):
        self.queries = []

    async def search(self, request):
        self.queries.append(request.query)
        return [RawPaperResult(
            source="europepmc",
            title="Europe PMC Title",
            doi="10.1016/j.celrep.2025.115926",
            pmid="40580478",
            pmcid="PMC12336999",
        )]


@pytest.mark.asyncio
async def test_doi_does_not_suppress_europepmc_pmcid_discovery():
    """fetch(doi) must still reach Europe PMC / PMCID discovery.

    METTL16 case: DOI 10.1016/j.celrep.2025.115926 -> PMID 40580478 ->
    PMCID PMC12336999. Providing only the DOI must not hide the PMCID path
    that unlocks JATS full text.
    """
    crossref = _FakeCrossref()
    epmc = _FakeEuropePMC()
    resolver = MetadataResolver(epmc, crossref)

    resolved = await resolver.resolve(
        ScholarFetchRequest(doi="10.1016/j.celrep.2025.115926")
    )

    # Crossref ran (DOI path) ...
    assert crossref.calls == ["10.1016/j.celrep.2025.115926"]
    # ... AND Europe PMC discovery ALSO ran despite the DOI being present.
    assert epmc.queries, "Europe PMC must still be queried when a DOI is present"
    # ... and the PMCID was discovered and merged in.
    assert resolved.pmcid == "PMC12336999"
    assert resolved.pmid == "40580478"


@pytest.mark.asyncio
async def test_adding_ids_never_removes_pmcid_path():
    """fetch(all ids) must not be worse than fetch(pmcid only).

    When a PMCID is already known, the resolver leaves it intact (the fetch
    service enriches from it directly); adding a DOI/PMID must not drop it.
    """
    crossref = _FakeCrossref()
    epmc = _FakeEuropePMC()
    resolver = MetadataResolver(epmc, crossref)

    resolved = await resolver.resolve(ScholarFetchRequest(
        doi="10.1016/j.celrep.2025.115926",
        pmid="40580478",
        pmcid="PMC12336999",
    ))
    assert resolved.pmcid == "PMC12336999"
    # PMCID already known -> no redundant Europe PMC discovery call needed.
    assert epmc.queries == []


# --------------------------------------------------------------------------
# abstract_only vs metadata_only invariant (section 17)
# --------------------------------------------------------------------------
def _fetch_service():
    svc = ScholarFetchService.__new__(ScholarFetchService)
    svc.settings = MagicMock()
    svc.settings.app_env = "dev"
    return svc


def test_status_abstract_only_requires_content():
    svc = _fetch_service()
    resp = svc._no_full_text(PaperMetadata(title="t", abstract="real abstract"), [])
    assert resp.full_text_status == "abstract_only"
    assert len(resp.content) > 0


def test_status_metadata_only_when_no_abstract():
    svc = _fetch_service()
    resp = svc._no_full_text(PaperMetadata(title="t", abstract=None), [])
    assert resp.full_text_status == "metadata_only"
    assert len(resp.content) == 0


def test_status_metadata_only_when_blank_abstract():
    # The impossible "abstract_only + content_length == 0" state must never recur.
    svc = _fetch_service()
    resp = svc._no_full_text(PaperMetadata(title="t", abstract="   "), [])
    assert resp.full_text_status == "metadata_only"
    assert len(resp.content) == 0


# --------------------------------------------------------------------------
# has_full_text semantic split (section 18)
# --------------------------------------------------------------------------
def test_oa_claim_without_candidate():
    p = PaperMetadata(title="t", is_open_access=True, full_text_candidates=[])
    assert p.is_open_access is True
    assert p.has_full_text_candidate is False
    assert p.full_text_retrievable is False


def test_candidate_exists_but_not_fetchable():
    p = PaperMetadata(title="t", full_text_candidates=[
        FullTextCandidate(source="x", format="pdf", access_type="closed", url="http://a")
    ])
    assert p.has_full_text_candidate is True
    assert p.full_text_retrievable is False


def test_candidate_exists_and_fetchable():
    p = PaperMetadata(title="t", full_text_candidates=[
        FullTextCandidate(source="x", format="xml", access_type="open", url="http://a")
    ])
    assert p.has_full_text_candidate is True
    assert p.full_text_retrievable is True


def test_computed_flags_survive_model_dump():
    from app.models.responses import SearchPaperMetadata
    p = PaperMetadata(title="t", full_text_candidates=[
        FullTextCandidate(source="x", format="html", access_type="open", url="http://a")
    ])
    dumped = p.model_dump()
    assert dumped["has_full_text_candidate"] is True
    assert dumped["full_text_retrievable"] is True
    search_view = SearchPaperMetadata.model_validate(dumped)
    assert search_view.has_full_text_candidate is True
    assert search_view.full_text_retrievable is True


# --------------------------------------------------------------------------
# Unpaywall is_oa backfill into is_open_access (section 9.1)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unpaywall_is_oa_backfills_is_open_access():
    """Publisher-hosted OA (hybrid/CC-BY) that Crossref/Europe PMC do not flag
    as OA must still be reported is_open_access=True when Unpaywall says so."""
    import httpx
    import respx
    from app.core.config import Settings
    from app.models.requests import ScholarFetchRequest
    from app.providers.crossref import CrossrefProvider
    from app.providers.europepmc import EuropePMCProvider
    from app.providers.unpaywall import UnpaywallProvider

    class _NonOAResolver:
        async def resolve(self, request):
            # Paper resolved but NOT flagged OA by Crossref/Europe PMC, and no
            # retrievable candidate (mirrors the CC-BY-but-unfetchable case).
            return PaperMetadata(
                title="Hybrid OA paper", doi="10.1/hybrid", is_open_access=False,
                abstract="Some abstract.",
            )

    with respx.mock:
        respx.get(url__regex=r"https://api\.unpaywall\.org/v2/.*").mock(
            return_value=httpx.Response(200, json={
                "is_oa": True,
                "oa_status": "hybrid",
                "best_oa_location": {
                    "url_for_pdf": None,
                    "url_for_landing_page": "https://publisher/landing",
                    "license": "cc-by",
                },
                "oa_locations": [],
            })
        )
        settings = Settings(app_env="dev", unpaywall_email="test@example.com")
        async with httpx.AsyncClient() as client:
            epmc = EuropePMCProvider(client, settings)
            service = ScholarFetchService(
                client, settings, epmc, UnpaywallProvider(client, settings),
                CrossrefProvider(client, settings), _NonOAResolver(),
            )
            resp = await service.fetch(ScholarFetchRequest(doi="10.1/hybrid"))

    # OA claim backfilled from Unpaywall ...
    assert resp.paper.is_open_access is True
    # ... but abstract_only status is honest: full text was not retrieved.
    assert resp.full_text_status == "abstract_only"

