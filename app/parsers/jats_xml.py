from lxml import etree

from app.core.errors import ParseFailed
from app.models.fulltext import FullTextSection
from app.utils.text import clean_text
from app.utils.ids import normalize_doi, normalize_pmcid


def classify_section(heading: str) -> str:
    value = heading.lower()
    mappings = [
        ("abstract", "abstract"), ("intro", "introduction"),
        ("method", "methods"), ("material", "methods"),
        ("result", "results"), ("discussion", "discussion"),
        ("conclu", "conclusion"), ("reference", "references"),
    ]
    return next((kind for token, kind in mappings if token in value), "other")


def _text(node) -> str:
    return clean_text(" ".join(node.itertext())) or ""


def parse_jats_xml(content: bytes | str) -> tuple[dict, list[FullTextSection]]:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
        root = etree.fromstring(content.encode() if isinstance(content, str) else content, parser)
    except Exception as exc:
        raise ParseFailed(f"Invalid JATS XML: {exc}") from exc
    title_nodes = root.xpath(".//*[local-name()='article-title']")
    journal_nodes = root.xpath(".//*[local-name()='journal-title']")
    article_ids = {}
    for node in root.xpath(".//*[local-name()='article-id']"):
        id_type = (node.get("pub-id-type") or node.get("id-type") or "").lower()
        value = _text(node)
        if id_type and value:
            article_ids[id_type] = value
    year_nodes = root.xpath(
        ".//*[local-name()='pub-date']/*[local-name()='year']"
        " | .//*[local-name()='history']//*[local-name()='date']/*[local-name()='year']"
    )
    month_nodes = root.xpath(".//*[local-name()='pub-date'][1]/*[local-name()='month']")
    day_nodes = root.xpath(".//*[local-name()='pub-date'][1]/*[local-name()='day']")
    year_text = _text(year_nodes[0]) if year_nodes else ""
    publication_date = None
    if year_text.isdigit():
        pieces = [year_text]
        if month_nodes and (month := _text(month_nodes[0])).isdigit():
            pieces.append(month.zfill(2))
            if day_nodes and (day := _text(day_nodes[0])).isdigit():
                pieces.append(day.zfill(2))
        publication_date = "-".join(pieces)
    authors = []
    for contrib in root.xpath(
        ".//*[local-name()='contrib' and (@contrib-type='author' or not(@contrib-type))]"
    ):
        surname = contrib.xpath(".//*[local-name()='surname']")
        given = contrib.xpath(".//*[local-name()='given-names']")
        name = " ".join(filter(None, [
            _text(given[0]) if given else "",
            _text(surname[0]) if surname else "",
        ]))
        if name:
            authors.append(name)
    article_type = (root.get("article-type") or "").lower()
    journal = _text(journal_nodes[0]) if journal_nodes else ""
    is_preprint = (
        "preprint" in article_type
        or journal.lower() in {"biorxiv", "medrxiv", "arxiv"}
    )
    metadata = {
        "title": _text(title_nodes[0]) if title_nodes else "",
        "abstract": None,
        "authors": authors,
        "doi": normalize_doi(article_ids.get("doi")),
        "pmid": article_ids.get("pmid"),
        "pmcid": normalize_pmcid(
            article_ids.get("pmc") or article_ids.get("pmcid")
        ),
        "year": int(year_text) if year_text.isdigit() else None,
        "publication_date": publication_date,
        "journal": journal or None,
        "is_preprint": is_preprint,
        "peer_reviewed": False if is_preprint else True,
        "review_status": "preprint" if is_preprint else "published",
    }
    sections = []
    abstract_nodes = root.xpath(".//*[local-name()='abstract']")
    if abstract_nodes and (text := _text(abstract_nodes[0])):
        metadata["abstract"] = text
        sections.append(FullTextSection(section_type="abstract", heading="Abstract", text=text))
    for sec in root.xpath(".//*[local-name()='body']/*[local-name()='sec']"):
        title = sec.xpath("./*[local-name()='title']")
        heading = _text(title[0]) if title else "Untitled"
        paragraphs = sec.xpath(".//*[local-name()='p']")
        text = clean_text(" ".join(_text(p) for p in paragraphs)) or ""
        if text:
            sections.append(FullTextSection(
                section_type=classify_section(heading), heading=heading, text=text
            ))
    refs = root.xpath(".//*[local-name()='ref-list']")
    if refs and (text := _text(refs[0])):
        sections.append(FullTextSection(section_type="references", heading="References", text=text))
    return metadata, sections
