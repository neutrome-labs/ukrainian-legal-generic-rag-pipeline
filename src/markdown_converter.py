"""Convert Ukrainian legal documents to one Markdown file per source document."""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class LegalDocument:
    """The complete source document uploaded for Cloudflare AI Search to chunk."""

    doc_id: str
    content: str
    title: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.md5(self.to_markdown().encode("utf-8")).hexdigest()[:12]

    def to_markdown(self) -> str:
        frontmatter = ["---", f"doc_id: {self.doc_id}", f'title: "{self.title}"']
        for key, value in self.metadata.items():
            if value is None:
                continue
            if isinstance(value, str):
                frontmatter.append(f'{key}: "{value}"')
            else:
                frontmatter.append(f"{key}: {value}")
        frontmatter.extend(["---", ""])
        return "\n".join(frontmatter) + self.content


class MarkdownConverter:
    """Cleans source text and preserves its legal structure as Markdown headings."""

    ARTICLE_PATTERN = re.compile(r"^(Стаття|стаття)\s*(\d+[\-\d]*\.?)\s*(.*)$", re.MULTILINE | re.UNICODE)
    CHAPTER_PATTERN = re.compile(r"^(Розділ|РОЗДІЛ|Глава|ГЛАВА)\s+([IVXLCDM\d]+\.?)\s*(.*)$", re.MULTILINE | re.UNICODE)
    SECTION_PATTERN = re.compile(r"^(Частина|ЧАСТИНА)\s+([IVXLCDM\d]+\.?)\s*(.*)$", re.MULTILINE | re.UNICODE)

    def html_to_text(self, html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "meta", "link"]):
            element.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for p in soup.find_all("p"):
            p.insert_after("\n\n")
        for div in soup.find_all("div"):
            div.insert_after("\n")
        text = unescape(soup.get_text())
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return re.sub(r" +\n", "\n", text).strip()

    def text_to_markdown(self, text: str) -> str:
        lines = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue
            article = self.ARTICLE_PATTERN.match(line)
            chapter = self.CHAPTER_PATTERN.match(line)
            section = self.SECTION_PATTERN.match(line)
            if article:
                _, number, heading = article.groups()
                lines.append(f"\n## Стаття {number.strip('.')} {heading}\n")
            elif chapter:
                _, number, heading = chapter.groups()
                lines.append(f"\n# Розділ {number.strip('.')} {heading}\n")
            elif section:
                _, number, heading = section.groups()
                lines.append(f"\n# Частина {number.strip('.')} {heading}\n")
            else:
                lines.append(line)
        return "\n".join(lines)

    def process_document(
        self,
        doc_id: str,
        title: str,
        text: str,
        structure: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LegalDocument:
        del structure  # The source body is uploaded intact; AI Search performs segmentation.
        if "<" in text and ">" in text:
            text = self.html_to_text(text)
        document_metadata = {"source": "data.rada.gov.ua", "language": "uk", **(metadata or {})}
        document = LegalDocument(doc_id=doc_id, title=title, content=self.text_to_markdown(text), metadata=document_metadata)
        logger.info("Prepared complete document %s for AI Search", doc_id)
        return document
