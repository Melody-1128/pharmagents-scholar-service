from lxml import etree

from app.core.errors import ParseFailed
from app.models.fulltext import FullTextSection
from app.parsers.jats_xml import classify_section
from app.utils.text import clean_text


def parse_tei_xml(content: bytes | str) -> tuple[dict, list[FullTextSection]]:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
        root = etree.fromstring(content.encode() if isinstance(content, str) else content, parser)
    except Exception as exc:
        raise ParseFailed(f"Invalid TEI XML: {exc}") from exc
    text = lambda node: clean_text(" ".join(node.itertext())) or ""
    titles = root.xpath(".//*[local-name()='teiHeader']//*[local-name()='title']")
    metadata = {"title": text(titles[0]) if titles else ""}
    sections = []
    abstracts = root.xpath(".//*[local-name()='profileDesc']//*[local-name()='abstract']")
    if abstracts and (value := text(abstracts[0])):
        sections.append(FullTextSection(section_type="abstract", heading="Abstract", text=value))
    for div in root.xpath(".//*[local-name()='body']//*[local-name()='div']"):
        heads = div.xpath("./*[local-name()='head']")
        heading = text(heads[0]) if heads else "Untitled"
        paragraphs = div.xpath("./*[local-name()='p']")
        value = clean_text(" ".join(text(p) for p in paragraphs)) or ""
        if value:
            sections.append(FullTextSection(
                section_type=classify_section(heading), heading=heading, text=value
            ))
    return metadata, sections
