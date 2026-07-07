import re

PMCID_PATTERN = re.compile(r"^PMC\d+$", re.IGNORECASE)
ARXIV_ID_PATTERN = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]+/\d{7})(?:v\d+)?$",
    re.IGNORECASE,
)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(" .,)") or None


def normalize_pmcid(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper()
    if normalized.isdigit():
        normalized = f"PMC{normalized}"
    return normalized if PMCID_PATTERN.fullmatch(normalized) else None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    normalized = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", normalized)
    normalized = re.sub(r"\.pdf$", "", normalized, flags=re.IGNORECASE)
    return normalized if ARXIV_ID_PATTERN.fullmatch(normalized) else None


def paper_id(doi=None, pmid=None, pmcid=None, openalex_id=None, title=None, arxiv_id=None) -> str:
    if doi := normalize_doi(doi):
        return f"doi:{doi}"
    if pmid:
        return f"pmid:{pmid}"
    if pmcid := normalize_pmcid(pmcid):
        return f"pmcid:{pmcid}"
    if openalex_id:
        return f"openalex:{openalex_id.rsplit('/', 1)[-1]}"
    if arxiv_id := normalize_arxiv_id(arxiv_id):
        return f"arxiv:{arxiv_id}"
    return f"title:{normalize_title(title or '')[:80]}"


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
