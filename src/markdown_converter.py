"""
Markdown Converter for Ukrainian Legal Documents
Converts HTML/text from Rada API to clean markdown chunks suitable for RAG
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple
from html import unescape
from bs4 import BeautifulSoup

from .config import ChunkingConfig, load_config

logger = logging.getLogger(__name__)


@dataclass
class DocumentChunk:
    """A single chunk of a legal document"""
    
    # Identification
    doc_id: str                     # Document nreg
    chunk_id: str                   # Unique chunk identifier
    chunk_index: int                # Position in document
    
    # Content
    content: str                    # Markdown content
    title: str                      # Document title
    
    # Structure info
    section_type: str               # article, chapter, preamble, etc.
    section_number: Optional[str]   # e.g., "Стаття 1", "Розділ II"
    section_title: Optional[str]    # Section heading if any
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def content_hash(self) -> str:
        """Get hash of content for deduplication"""
        return hashlib.md5(self.content.encode('utf-8')).hexdigest()[:12]
    
    def to_markdown(self) -> str:
        """Generate full markdown with frontmatter"""
        frontmatter = [
            "---",
            f"doc_id: {self.doc_id}",
            f"chunk_id: {self.chunk_id}",
            f"chunk_index: {self.chunk_index}",
            f"title: \"{self.title}\"",
            f"section_type: {self.section_type}",
        ]
        
        if self.section_number:
            frontmatter.append(f"section_number: \"{self.section_number}\"")
        if self.section_title:
            frontmatter.append(f"section_title: \"{self.section_title}\"")
        
        for key, value in self.metadata.items():
            if isinstance(value, str):
                frontmatter.append(f"{key}: \"{value}\"")
            else:
                frontmatter.append(f"{key}: {value}")
        
        frontmatter.append("---")
        frontmatter.append("")
        
        return "\n".join(frontmatter) + self.content
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "title": self.title,
            "section_type": self.section_type,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "metadata": self.metadata,
            "content_hash": self.content_hash
        }


class MarkdownConverter:
    """Converts Ukrainian legal documents to markdown chunks"""
    
    # Patterns for legal document structure
    ARTICLE_PATTERN = re.compile(
        r'^(Стаття|стаття)\s*(\d+[\-\d]*\.?)\s*(.*)$',
        re.MULTILINE | re.UNICODE
    )
    
    CHAPTER_PATTERN = re.compile(
        r'^(Розділ|РОЗДІЛ|Глава|ГЛАВА)\s+([IVXLCDM\d]+\.?)\s*(.*)$',
        re.MULTILINE | re.UNICODE
    )
    
    SECTION_PATTERN = re.compile(
        r'^(Частина|ЧАСТИНА)\s+([IVXLCDM\d]+\.?)\s*(.*)$',
        re.MULTILINE | re.UNICODE
    )
    
    PARAGRAPH_PATTERN = re.compile(
        r'^(\d+)\.\s+(.+)$',
        re.MULTILINE | re.UNICODE
    )
    
    SUBPARAGRAPH_PATTERN = re.compile(
        r'^(\d+)\)\s+(.+)$',
        re.MULTILINE | re.UNICODE
    )
    
    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or load_config().chunking
    
    def html_to_text(self, html: str) -> str:
        """Convert HTML to clean text"""
        if not html:
            return ""
        
        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements
        for element in soup(['script', 'style', 'meta', 'link']):
            element.decompose()
        
        # Handle special elements
        for br in soup.find_all('br'):
            br.replace_with('\n')
        
        for p in soup.find_all('p'):
            p.insert_after('\n\n')
        
        for div in soup.find_all('div'):
            div.insert_after('\n')
        
        # Get text
        text = soup.get_text()
        
        # Clean up
        text = unescape(text)
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Multiple newlines
        text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces
        text = re.sub(r' +\n', '\n', text)  # Trailing spaces
        
        return text.strip()
    
    def extract_structure_from_api(
        self, 
        structure: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract document structure from API response.
        The API provides structured data with tree_id, type (TY, GL, ST, etc.)
        """
        if not structure:
            return []
        
        sections = []
        
        # Structure is a list of elements with type info
        for elem in structure if isinstance(structure, list) else [structure]:
            section = {
                'id': elem.get('id', ''),
                'tree_id': elem.get('tree_id', ''),
                'type': elem.get('typ', ''),  # TY, GL, ST, CH, etc.
                'level': elem.get('level', 0),
                'text': self.html_to_text(elem.get('text', '')),
                'line': elem.get('line', ''),
                'stru': elem.get('stru', ''),
            }
            sections.append(section)
        
        return sections
    
    def text_to_markdown(self, text: str, title: str = "") -> str:
        """Convert plain text to clean markdown"""
        if not text:
            return ""
        
        lines = text.split('\n')
        md_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                md_lines.append('')
                continue
            
            # Check for article headings
            article_match = self.ARTICLE_PATTERN.match(line)
            if article_match:
                _, num, title_part = article_match.groups()
                md_lines.append(f"\n## Стаття {num.strip('.')} {title_part}\n")
                continue
            
            # Check for chapter headings
            chapter_match = self.CHAPTER_PATTERN.match(line)
            if chapter_match:
                _, num, title_part = chapter_match.groups()
                md_lines.append(f"\n# Розділ {num.strip('.')} {title_part}\n")
                continue
            
            # Check for section headings
            section_match = self.SECTION_PATTERN.match(line)
            if section_match:
                _, num, title_part = section_match.groups()
                md_lines.append(f"\n# Частина {num.strip('.')} {title_part}\n")
                continue
            
            # Regular paragraph
            md_lines.append(line)
        
        return '\n'.join(md_lines)
    
    def split_into_chunks(
        self,
        text: str,
        doc_id: str,
        title: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[DocumentChunk]:
        """Split document text into chunks by articles"""
        chunks = []
        metadata = metadata or {}
        
        # First, try to split by articles
        article_splits = self._split_by_articles(text)
        
        if len(article_splits) > 1:
            # Document has clear article structure
            for i, (section_num, section_title, section_text) in enumerate(article_splits):
                if not section_text.strip():
                    continue
                
                chunk = DocumentChunk(
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}_{i:04d}",
                    chunk_index=i,
                    content=self.text_to_markdown(section_text, title),
                    title=title,
                    section_type="article" if section_num else "preamble",
                    section_number=section_num,
                    section_title=section_title,
                    metadata=metadata.copy()
                )
                chunks.append(chunk)
        else:
            # No article structure, chunk by size
            chunks = self._chunk_by_size(text, doc_id, title, metadata)
        
        return chunks
    
    def _split_by_articles(self, text: str) -> List[Tuple[Optional[str], Optional[str], str]]:
        """Split text by article headings"""
        splits = []
        
        # Find all article matches
        matches = list(self.ARTICLE_PATTERN.finditer(text))
        
        if not matches:
            return [(None, None, text)]
        
        # Content before first article (preamble)
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                splits.append((None, "Преамбула", preamble))
        
        # Process each article
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            
            article_num = f"Стаття {match.group(2).strip('.')}"
            article_title = match.group(3).strip() if match.group(3) else None
            article_text = text[start:end].strip()
            
            splits.append((article_num, article_title, article_text))
        
        return splits
    
    def _chunk_by_size(
        self,
        text: str,
        doc_id: str,
        title: str,
        metadata: Dict[str, Any]
    ) -> List[DocumentChunk]:
        """Chunk text by size when no structure is available"""
        chunks = []
        
        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', text)
        
        current_chunk = []
        current_size = 0
        chunk_index = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            para_size = len(para)
            
            # Check if adding this paragraph would exceed max size
            if current_size + para_size > self.config.max_chunk_size and current_chunk:
                # Save current chunk
                chunk_text = '\n\n'.join(current_chunk)
                chunk = DocumentChunk(
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}_{chunk_index:04d}",
                    chunk_index=chunk_index,
                    content=self.text_to_markdown(chunk_text, title),
                    title=title,
                    section_type="paragraph",
                    section_number=None,
                    section_title=None,
                    metadata=metadata.copy()
                )
                chunks.append(chunk)
                chunk_index += 1
                
                # Start new chunk with overlap
                if self.config.chunk_overlap > 0 and current_chunk:
                    overlap_text = current_chunk[-1]
                    if len(overlap_text) <= self.config.chunk_overlap:
                        current_chunk = [overlap_text]
                        current_size = len(overlap_text)
                    else:
                        current_chunk = []
                        current_size = 0
                else:
                    current_chunk = []
                    current_size = 0
            
            current_chunk.append(para)
            current_size += para_size
        
        # Don't forget the last chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            if len(chunk_text) >= self.config.min_chunk_size:
                chunk = DocumentChunk(
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}_{chunk_index:04d}",
                    chunk_index=chunk_index,
                    content=self.text_to_markdown(chunk_text, title),
                    title=title,
                    section_type="paragraph",
                    section_number=None,
                    section_title=None,
                    metadata=metadata.copy()
                )
                chunks.append(chunk)
        
        return chunks
    
    def process_document(
        self,
        doc_id: str,
        title: str,
        text: str,
        structure: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[DocumentChunk]:
        """
        Process a full document into markdown chunks.
        Uses structure if available, falls back to text parsing.
        """
        metadata = metadata or {}
        
        # Add document-level metadata
        base_metadata = {
            "source": "data.rada.gov.ua",
            "language": "uk",
        }
        base_metadata.update(metadata)
        
        # Convert HTML to text if needed
        if '<' in text and '>' in text:
            text = self.html_to_text(text)
        
        # Split into chunks
        chunks = self.split_into_chunks(text, doc_id, title, base_metadata)
        
        logger.info(f"Document {doc_id} split into {len(chunks)} chunks")
        
        return chunks


class ConstitutionProcessor:
    """Special processor for the Constitution of Ukraine"""
    
    def __init__(self, converter: Optional[MarkdownConverter] = None):
        self.converter = converter or MarkdownConverter()
    
    def process(
        self,
        text: str,
        structure: Optional[Dict[str, Any]] = None
    ) -> List[DocumentChunk]:
        """
        Process the Constitution with special handling.
        The Constitution has:
        - Preamble
        - 15 Chapters (Розділ)
        - 161 Articles (Стаття)
        - Final and Transitional Provisions
        """
        doc_id = "254к_96-вр"
        title = "Конституція України"
        
        metadata = {
            "doc_type": "constitution",
            "adopted": "1996-06-28",
            "importance": "fundamental",
        }
        
        # Clean text
        if '<' in text and '>' in text:
            text = self.converter.html_to_text(text)
        
        chunks = []
        chunk_index = 0
        
        # Extract chapters and articles
        chapter_pattern = re.compile(
            r'(Розділ\s+[IVXLCDM]+\.?\s*[^\n]+)',
            re.UNICODE
        )
        
        # Split by chapters first
        chapter_splits = chapter_pattern.split(text)
        
        current_chapter = None
        
        for i, part in enumerate(chapter_splits):
            part = part.strip()
            if not part:
                continue
            
            # Check if this is a chapter header
            if chapter_pattern.match(part):
                current_chapter = part
                continue
            
            # Process articles within this chapter
            article_pattern = re.compile(
                r'(Стаття\s+\d+\.?\s*)',
                re.UNICODE
            )
            
            article_splits = article_pattern.split(part)
            
            for j, article_part in enumerate(article_splits):
                article_part = article_part.strip()
                if not article_part:
                    continue
                
                # Check if this is an article header
                article_match = re.match(r'Стаття\s+(\d+)', article_part)
                if article_match:
                    article_num = article_match.group(1)
                    # Get the content that follows
                    if j + 1 < len(article_splits):
                        article_content = article_splits[j + 1].strip()
                        
                        # Create chunk for this article
                        chunk = DocumentChunk(
                            doc_id=doc_id,
                            chunk_id=f"constitution_article_{article_num}",
                            chunk_index=chunk_index,
                            content=f"## Стаття {article_num}\n\n{article_content}",
                            title=title,
                            section_type="article",
                            section_number=f"Стаття {article_num}",
                            section_title=None,
                            metadata={
                                **metadata,
                                "chapter": current_chapter
                            }
                        )
                        chunks.append(chunk)
                        chunk_index += 1
        
        # If structure parsing didn't work well, fall back to simpler approach
        if len(chunks) < 50:  # Constitution has 161 articles
            logger.warning("Fallback to simple article splitting for Constitution")
            chunks = self.converter.split_into_chunks(
                text, doc_id, title, metadata
            )
        
        logger.info(f"Constitution processed into {len(chunks)} chunks")
        return chunks


def test_converter():
    """Test the markdown converter"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample legal text
    sample_text = """
    Розділ I
    ЗАГАЛЬНІ ЗАСАДИ
    
    Стаття 1. Україна є суверенна і незалежна, демократична, соціальна, правова держава.
    
    Стаття 2. Суверенітет України поширюється на всю її територію.
    
    Україна є унітарною державою.
    
    Територія України в межах існуючого кордону є цілісною і недоторканною.
    
    Стаття 3. Людина, її життя і здоров'я, честь і гідність, недоторканність і безпека визнаються в Україні найвищою соціальною цінністю.
    
    Права і свободи людини та їх гарантії визначають зміст і спрямованість діяльності держави.
    """
    
    converter = MarkdownConverter()
    
    # Test splitting
    chunks = converter.split_into_chunks(
        sample_text,
        doc_id="test-doc",
        title="Тестовий документ",
        metadata={"test": True}
    )
    
    print(f"Created {len(chunks)} chunks:")
    for chunk in chunks:
        print(f"\n--- Chunk {chunk.chunk_index} ({chunk.section_type}) ---")
        print(f"Section: {chunk.section_number}")
        print(chunk.content[:200] + "...")


if __name__ == "__main__":
    test_converter()
