import logging

import httpx

from app.core.config import Settings
from app.core.errors import ParseFailed
from app.models.fulltext import FullTextSection, RetrievalInfo
from app.models.paper import FullTextCandidate, PaperMetadata
from app.models.requests import PaperFetchInput, ScholarFetchRequest
from app.models.responses import ScholarFetchBatchResponse, ScholarFetchResponse, ScholarFetchResult
from app.parsers.html import parse_html
from app.parsers.jats_xml import parse_jats_xml
from app.parsers.pdf import parse_pdf
from app.parsers.tei_xml import parse_tei_xml
from app.providers.crossref import CrossrefProvider
from app.providers.europepmc import EuropePMCProvider
from app.providers.unpaywall import UnpaywallProvider
from app.services.resolver import MetadataResolver
from app.storage.cache import Cache
from app.utils.ids import normalize_pmcid, paper_id

logger = logging.getLogger(__name__)


class ScholarFetchService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        europepmc: EuropePMCProvider,
        unpaywall: UnpaywallProvider,
        crossref: CrossrefProvider,
        resolver: MetadataResolver,
        cache: Cache | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.europepmc = europepmc
        self.unpaywall = unpaywall
        self.crossref = crossref
        self.resolver = resolver
        self.cache = cache

    async def fetch(self, request: ScholarFetchRequest) -> ScholarFetchResponse:
        key = Cache.key({"schema_version": 4, **request.model_dump()})
        if self.cache and (cached := self.cache.get("fulltext", key)):
            return ScholarFetchResponse.model_validate(cached)
        paper = await self.resolver.resolve(request)
        paper.paper_id = paper_id(
            paper.doi, paper.pmid, paper.pmcid, paper.openalex_id,
            paper.title, paper.arxiv_id,
        )
        warnings = []
        if (
            paper.server in {"biorxiv", "medrxiv"}
            and (paper.doi or request.doi)
            and not (
                normalize_pmcid(paper.pmcid)
                or normalize_pmcid(request.pmcid)
            )
        ):
            try:
                epmc_paper = await self.europepmc.find_by_doi(paper.doi or request.doi)
                if epmc_paper and epmc_paper.pmcid:
                    self._enrich_from_paper(
                        paper, epmc_paper.normalize(), "europepmc"
                    )
                    content, url = await self.europepmc.fetch_xml(epmc_paper.pmcid)
                    xml_metadata, sections = parse_jats_xml(content)
                    self._enrich_from_metadata(paper, xml_metadata, "europepmc_xml")
                    if self._contains_full_text(sections):
                        paper.pmcid = paper.pmcid or epmc_paper.pmcid
                        paper.pmid = paper.pmid or epmc_paper.pmid
                        response = self._success(
                            paper, sections, "europepmc", "xml", url,
                            None, request.max_chars, warnings,
                        )
                        return self._cache(key, response)
            except Exception as exc:
                warnings.append(
                    f"Europe PMC DOI lookup/XML failed: {type(exc).__name__}: {exc}"
                )
        valid_pmcid = normalize_pmcid(paper.pmcid) or normalize_pmcid(request.pmcid)
        if valid_pmcid:
            try:
                epmc_paper = await self.europepmc.find_by_pmcid(valid_pmcid)
                if epmc_paper:
                    self._enrich_from_paper(
                        paper, epmc_paper.normalize(), "europepmc"
                    )
            except Exception as exc:
                logger.debug("Europe PMC metadata lookup failed: %s", exc)
            try:
                content, url = await self.europepmc.fetch_xml(valid_pmcid)
                xml_metadata, sections = parse_jats_xml(content)
                self._enrich_from_metadata(paper, xml_metadata, "europepmc_xml")
                if self._contains_full_text(sections):
                    response = self._success(
                        paper, sections, "europepmc", "xml", url, None, request.max_chars, warnings
                    )
                    return self._cache(key, response)
            except Exception as exc:
                warnings.append(f"Europe PMC XML failed: {type(exc).__name__}: {exc}")
        candidates: list[FullTextCandidate] = []
        if paper.doi or request.doi:
            doi = paper.doi or request.doi
            try:
                unpaywall_result = await self.unpaywall.resolve(doi)
                candidates.extend(unpaywall_result.candidates)
                # Backfill the OA claim: Unpaywall knows about publisher-hosted
                # (hybrid/gold) OA that Crossref/Europe PMC may not report. This
                # is the bibliographic OA claim only; it does not assert the
                # pipeline can retrieve the file (see full_text_retrievable).
                if unpaywall_result.is_oa:
                    paper.is_open_access = True
            except Exception as exc:
                warnings.append(f"Unpaywall failed: {type(exc).__name__}: {exc}")
            try:
                _, links = await self.crossref.metadata(doi)
                candidates.extend(links)
            except Exception as exc:
                warnings.append(f"Crossref links failed: {type(exc).__name__}: {exc}")
        candidates.extend(paper.full_text_candidates)
        format_preference = (
            ["xml", "html", "pdf"]
            if paper.server in {"biorxiv", "medrxiv"}
            else request.prefer_formats
        )
        formats = {fmt: i for i, fmt in enumerate(format_preference)}
        candidates.sort(key=lambda c: (formats.get(c.format, 99), c.priority))
        for candidate in candidates:
            if candidate.access_type != "open":
                warnings.append(
                    f"Skipped {candidate.source} link because open access was not verified."
                )
                continue
            if candidate.format == "pdf" and (not request.allow_pdf or not self.settings.enable_pdf_fetch):
                continue
            try:
                response = await self._fetch_candidate(paper, candidate, request.max_chars, warnings)
                if (
                    candidate.format == "pdf"
                    and paper.server in {"biorxiv", "medrxiv"}
                ):
                    failed_formats = [
                        fmt for fmt in ("xml", "html")
                        if any(
                            f"{paper.server} {fmt} failed:" in warning
                            for warning in warnings
                        )
                    ]
                    if failed_formats:
                        response.warnings.append(
                            f"{'/'.join(failed_formats).upper()} failed; "
                            f"{paper.server} PDF fallback was used."
                        )
                return self._cache(key, response)
            except Exception as exc:
                warnings.append(
                    f"{candidate.source} {candidate.format} failed: {type(exc).__name__}: {exc}"
                )
        response = self._no_full_text(paper, warnings)
        return self._cache(key, response)

    async def _fetch_candidate(
        self, paper: PaperMetadata, candidate: FullTextCandidate, max_chars: int, warnings: list[str]
    ) -> ScholarFetchResponse:
        if not candidate.url:
            raise ParseFailed("Candidate has no URL")
        response = await self.client.get(candidate.url)
        response.raise_for_status()
        content = response.content
        if len(content) > self.settings.max_download_bytes:
            raise ParseFailed("Downloaded file exceeds size limit")
        content_type = response.headers.get("content-type", "").lower()
        fmt = candidate.format
        if content.startswith(b"%PDF") or "pdf" in content_type:
            fmt, (_, sections) = "pdf", parse_pdf(content)
        elif "html" in content_type or content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            fmt, (_, sections) = "html", parse_html(content)
        elif b"<TEI" in content[:1000] or b"<tei" in content[:1000]:
            fmt, (_, sections) = "xml", parse_tei_xml(content)
        else:
            fmt, (_, sections) = "xml", parse_jats_xml(content)
        if not sections:
            raise ParseFailed("Parser produced no sections")
        if paper.is_preprint and not self._contains_full_text(sections):
            raise ParseFailed(
                "Candidate contained metadata/abstract but no parseable full text"
            )
        return self._success(
            paper, sections, candidate.source, fmt, candidate.url,
            candidate.license, max_chars, warnings
        )

    def _success(self, paper, sections, source, fmt, url, license_value, max_chars, warnings):
        paper.has_full_text = True
        paper.is_open_access = True
        paper.source_hits = list(dict.fromkeys(paper.source_hits + [source]))
        if not any(
            candidate.source == source
            and candidate.format == fmt
            and candidate.url == url
            for candidate in paper.full_text_candidates
        ):
            paper.full_text_candidates.append(FullTextCandidate(
                source=source,
                format=fmt,
                priority=1,
                url=url,
                license=license_value,
            ))
        paper.field_sources = paper.field_sources or {}
        paper.field_sources["has_full_text"] = [source]
        paper.field_sources["is_open_access"] = [source]
        paper.paper_id = paper_id(
            paper.doi, paper.pmid, paper.pmcid, paper.openalex_id,
            paper.title, paper.arxiv_id,
        )
        plain = "\n\n".join(
            f"{section.heading}\n{section.text}" for section in sections
        )[:max_chars]
        response = ScholarFetchResponse(
            paper=paper,
            full_text_status="success",
            retrieval=RetrievalInfo(
                source=source, format=fmt, license=license_value, url=url
            ),
            content=plain,
            warnings=list(warnings),
        )
        if self.settings.app_env.lower() != "dev":
            response.paper.field_sources = None
            response.paper.metadata_conflicts = None
        return response

    def _enrich_from_paper(
        self, paper: PaperMetadata, metadata: PaperMetadata, source: str
    ) -> None:
        values = {
            "title": metadata.title,
            "abstract": metadata.abstract,
            "authors": metadata.authors,
            "doi": metadata.doi,
            "pmid": metadata.pmid,
            "pmcid": metadata.pmcid,
            "year": metadata.year,
            "journal": metadata.journal,
            "publication_date": metadata.publication_date,
            "is_preprint": metadata.is_preprint,
            "peer_reviewed": metadata.peer_reviewed,
            "review_status": metadata.review_status,
        }
        self._enrich_from_metadata(paper, values, source)

    def _enrich_from_metadata(
        self, paper: PaperMetadata, metadata: dict, source: str
    ) -> None:
        paper.field_sources = paper.field_sources or {}
        paper.metadata_conflicts = paper.metadata_conflicts or []
        placeholder_titles = {
            "unknown paper",
            str(paper.pmcid or "").lower(),
            str(paper.pmid or "").lower(),
            str(paper.doi or "").lower(),
        }
        for field in (
            "title", "abstract", "doi", "pmid", "pmcid", "year", "journal",
            "publication_date", "is_preprint", "peer_reviewed", "review_status",
        ):
            new = metadata.get(field)
            if new in (None, ""):
                continue
            current = getattr(paper, field)
            should_replace = current in (None, "")
            if field == "title" and str(current).lower() in placeholder_titles:
                should_replace = True
            if field == "abstract" and len(str(new)) > len(str(current or "")):
                should_replace = True
            if should_replace or current == new:
                setattr(paper, field, new)
                paper.field_sources[field] = list(dict.fromkeys(
                    paper.field_sources.get(field, []) + [source]
                ))
            elif field in {"doi", "pmid", "pmcid"} and str(current).lower() != str(new).lower():
                paper.metadata_conflicts.append({
                    "field": field,
                    "kept_value": str(current),
                    "kept_source": (paper.field_sources.get(field) or ["request"])[0],
                    "rejected_value": str(new),
                    "rejected_source": source,
                })
        authors = metadata.get("authors") or []
        if authors:
            paper.authors = list(dict.fromkeys(paper.authors + authors))
            paper.field_sources["authors"] = list(dict.fromkeys(
                paper.field_sources.get("authors", []) + [source]
            ))
        paper.source_hits = list(dict.fromkeys(paper.source_hits + [source]))

    @staticmethod
    def _contains_full_text(sections: list[FullTextSection]) -> bool:
        return any(
            section.section_type != "abstract" and len(section.text.strip()) >= 50
            for section in sections
        )

    def _no_full_text(self, paper: PaperMetadata, warnings: list[str]) -> ScholarFetchResponse:
        """Return the correct no-full-text status.

        Invariant enforced here:
            abstract_only  => non-empty abstract AND content_length > 0
            metadata_only  => paper located but no readable abstract/full text
                              (content may be empty)

        The previous implementation always returned ``abstract_only`` even when
        the abstract was empty, producing the impossible ``abstract_only`` +
        ``content_length == 0`` state. Callers relied on that status to describe
        evidence "according to the abstract" when there was none.
        """
        abstract_text = (paper.abstract or "").strip()
        if abstract_text:
            content = self._sections_to_content([
                FullTextSection(
                    section_type="abstract", heading="Abstract", text=paper.abstract
                )
            ])
            status = "abstract_only"
            note = "No open full text found; a non-empty abstract is available."
        else:
            content = ""
            status = "metadata_only"
            note = (
                "No open full text and no readable abstract were obtained; "
                "only bibliographic metadata is available."
            )
        response = ScholarFetchResponse(
            paper=paper,
            full_text_status=status,
            retrieval=RetrievalInfo(
                source=paper.server or next(iter(paper.source_hits), "metadata"),
                format="abstract" if status == "abstract_only" else "metadata",
                url=paper.landing_url,
            ) if paper.is_preprint else None,
            content=content,
            warnings=warnings + [note],
        )
        if self.settings.app_env.lower() != "dev":
            response.paper.field_sources = None
            response.paper.metadata_conflicts = None
        return response

    def _cache(self, key: str, response: ScholarFetchResponse) -> ScholarFetchResponse:
        if self.cache:
            self.cache.set("fulltext", key, response.model_dump(mode="json"), ttl=86_400)
        return response

    async def fetch_batch(self, request: ScholarFetchRequest) -> ScholarFetchBatchResponse:
        papers = request.papers or [PaperFetchInput(**request.model_dump(
            include={
                "doi", "pmid", "pmcid", "openalex_id", "semantic_scholar_id",
                "arxiv_id", "title", "abstract", "source", "category",
                "landing_url", "pdf_url", "is_preprint",
            }
        ))]
        results: list[ScholarFetchResult] = []
        for paper_input in papers:
            try:
                single_request = request.single_paper_request(paper_input)
                response = await self.fetch(single_request)
                failed = response.full_text_status in {"failed", "error"}
                results.append(ScholarFetchResult(
                    input=paper_input,
                    paper=response.paper,
                    full_text_status=response.full_text_status,
                    retrieval=response.retrieval,
                    content=response.content,
                    warnings=response.warnings,
                    error=None if not failed else "; ".join(response.warnings),
                ))
            except Exception as exc:
                placeholder = PaperMetadata(
                    paper_id="",
                    title=paper_input.title or paper_input.doi or paper_input.pmid
                    or paper_input.pmcid or paper_input.openalex_id
                    or paper_input.arxiv_id or "Unknown paper",
                    doi=paper_input.doi,
                    pmid=paper_input.pmid,
                    pmcid=paper_input.pmcid,
                    openalex_id=paper_input.openalex_id,
                    semantic_scholar_id=paper_input.semantic_scholar_id,
                    arxiv_id=paper_input.arxiv_id,
                    abstract=paper_input.abstract,
                    server=paper_input.source,
                    category=paper_input.category,
                    landing_url=paper_input.landing_url,
                    pdf_url=paper_input.pdf_url,
                    is_preprint=paper_input.is_preprint,
                )
                results.append(ScholarFetchResult(
                    input=paper_input,
                    paper=placeholder,
                    full_text_status="failed",
                    retrieval=None,
                    content="",
                    warnings=[f"{type(exc).__name__}: {exc}"],
                    error=f"{type(exc).__name__}: {exc}",
                ))
        succeeded = sum(
            result.full_text_status in {"success", "abstract_only", "link_only"}
            for result in results
        )
        return ScholarFetchBatchResponse(
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=results,
        )

    @staticmethod
    def _sections_to_content(sections: list[FullTextSection]) -> str:
        return "\n\n".join(
            f"{section.heading}\n{section.text}"
            for section in sections
            if section.text.strip()
        )
