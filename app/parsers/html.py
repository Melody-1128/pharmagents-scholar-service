from bs4 import BeautifulSoup

from app.models.fulltext import FullTextSection
from app.parsers.jats_xml import classify_section
from app.utils.text import clean_text


def parse_html(content: bytes | str) -> tuple[dict, list[FullTextSection]]:
    soup = BeautifulSoup(content, "lxml")
    for node in soup(["script", "style", "nav", "footer", "aside", "form"]):
        node.decompose()
    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    container = soup.find("article") or soup.find("main") or soup.body or soup
    sections = []
    current_heading = "Full text"
    chunks = []
    for node in container.find_all(["h1", "h2", "h3", "p"], recursive=True):
        if node.name.startswith("h"):
            if chunks:
                sections.append(FullTextSection(
                    section_type=classify_section(current_heading),
                    heading=current_heading,
                    text=clean_text(" ".join(chunks)) or "",
                ))
                chunks = []
            current_heading = clean_text(node.get_text(" ", strip=True)) or "Untitled"
        elif text := clean_text(node.get_text(" ", strip=True)):
            chunks.append(text)
    if chunks:
        sections.append(FullTextSection(
            section_type=classify_section(current_heading),
            heading=current_heading,
            text=clean_text(" ".join(chunks)) or "",
        ))
    return {"title": title}, [section for section in sections if section.text]
