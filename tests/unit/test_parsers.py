from pathlib import Path

import pytest

from app.core.errors import ParseFailed
from app.parsers.html import parse_html
from app.parsers.jats_xml import parse_jats_xml
from app.parsers.pdf import parse_pdf
from app.parsers.tei_xml import parse_tei_xml

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_jats_parser_extracts_sections():
    metadata, sections = parse_jats_xml((FIXTURES / "jats.xml").read_bytes())
    assert metadata["title"] == "A test cancer paper"
    assert {section.section_type for section in sections} >= {
        "abstract", "introduction", "methods", "results", "discussion", "references"
    }


def test_jats_parser_extracts_front_matter_metadata():
    xml = b"""
    <article article-type="research-article">
      <front>
        <journal-meta><journal-title>Journal of Testing</journal-title></journal-meta>
        <article-meta>
          <article-id pub-id-type="doi">10.1000/test</article-id>
          <article-id pub-id-type="pmid">12345</article-id>
          <article-id pub-id-type="pmc">PMC67890</article-id>
          <title-group><article-title>Real article title</article-title></title-group>
          <contrib-group><contrib contrib-type="author"><name>
            <surname>Doe</surname><given-names>Jane</given-names>
          </name></contrib></contrib-group>
          <pub-date pub-type="epub"><year>2024</year><month>5</month><day>9</day></pub-date>
          <abstract><p>Real abstract.</p></abstract>
        </article-meta>
      </front>
      <body><sec><title>Results</title><p>This body section contains enough
      text to represent successfully parsed full article content.</p></sec></body>
    </article>
    """
    metadata, _ = parse_jats_xml(xml)
    assert metadata["title"] == "Real article title"
    assert metadata["doi"] == "10.1000/test"
    assert metadata["pmid"] == "12345"
    assert metadata["pmcid"] == "PMC67890"
    assert metadata["year"] == 2024
    assert metadata["publication_date"] == "2024-05-09"
    assert metadata["journal"] == "Journal of Testing"
    assert metadata["authors"] == ["Jane Doe"]
    assert metadata["is_preprint"] is False


def test_tei_parser_extracts_sections():
    metadata, sections = parse_tei_xml((FIXTURES / "tei.xml").read_bytes())
    assert metadata["title"] == "TEI test paper"
    assert [section.section_type for section in sections] == ["abstract", "introduction", "results"]


def test_html_parser_removes_navigation():
    metadata, sections = parse_html(
        "<html><head><title>Paper</title></head><body><nav>Noise</nav>"
        "<article><h2>Methods</h2><p>Useful text.</p></article></body></html>"
    )
    assert metadata["title"] == "Paper"
    assert sections[0].section_type == "methods"
    assert "Noise" not in sections[0].text


def test_pdf_parser_rejects_non_pdf():
    with pytest.raises(ParseFailed):
        parse_pdf(b"<html>not pdf</html>")
