"""Bounded, cached downloads from the official open-data file host."""

import hashlib
import os
import re
import tempfile
import time
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from striprtf.striprtf import rtf_to_text

_CHARSETS = {"windows-1251": "cp1251", "cp1251": "cp1251", "utf-8": "utf-8", "utf8": "utf-8"}
_DECLARED = re.compile(rb"charset=([A-Za-z0-9_-]+)")


class DecisionTextLoader:
    def __init__(self, cache_dir: str | Path, delay: float = 6.0, offline: bool = False):
        if delay < 0:
            raise ValueError("delay must be non-negative")
        self.cache_dir = Path(cache_dir)
        self.delay = delay
        self.offline = offline
        self.session = requests.Session()
        self.last_request = None
        self.max_bytes = 20 * 1024 * 1024

    def close(self):
        self.session.close()

    def url_kind(self, url: str) -> str:
        parsed = urlsplit(url)
        suffix = Path(parsed.path).suffix.lower()
        if (parsed.scheme != "https" or parsed.netloc != "od.reyestr.court.gov.ua"
                or not parsed.path.startswith("/files/") or suffix not in (".rtf", ".html")
                or parsed.query or parsed.fragment):
            raise ValueError(f"Unsupported document URL: {url!r}")
        return suffix[1:]

    def load(self, url: str) -> str:
        kind = self.url_kind(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache_dir / key[:2] / f"{key}.{kind}"
        if path.exists():
            if path.stat().st_size > self.max_bytes:
                raise ValueError(f"Cached document exceeds size limit: {path}")
            data = path.read_bytes()
        elif self.offline:
            raise ValueError(f"Document not cached for {url}; run without --offline to download")
        else:
            if self.last_request is not None:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            # No automatic retries/redirects: stop on access restrictions or rate limits.
            with self.session.get(url, stream=True, timeout=(10, 60), allow_redirects=False,
                                  headers={"User-Agent": "UkrainianLegalRAG/1.0"}) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError(f"Unexpected HTTP {response.status_code} for {url}")
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        raise ValueError(f"Document exceeds {self.max_bytes} bytes: {url}")
                data = bytes(data)
            # Validate before caching; an error page must not become a document.
            text = self.convert(data, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = stream.name
                    stream.write(data)
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return text
        return self.convert(data, kind)

    @staticmethod
    def convert(data: bytes, kind: str) -> str:
        if kind == "rtf":
            if not data.lstrip().startswith(b"{\\rtf"):
                raise ValueError("Expected RTF document; received another format or an access page")
            # Turn raw high bytes into RTF hex escapes so both raw Cyrillic and
            # already escaped text use the declared codepage (including font charsets).
            rtf = "".join(chr(byte) if byte < 128 else "\\'%02x" % byte for byte in data)
            text = rtf_to_text(rtf, errors="strict").strip()
        elif kind == "html":
            text = DecisionTextLoader._html_to_text(data)
        else:
            raise ValueError(f"Unknown document kind: {kind!r}")
        if not text:
            raise ValueError("Document contains no readable text")
        return text

    @staticmethod
    def _html_to_text(data: bytes) -> str:
        declared = _DECLARED.search(data[:2048])
        name = declared[1].decode("ascii", "strict").lower() if declared else "windows-1251"
        encoding = _CHARSETS.get(name)
        if encoding is None:
            raise ValueError(f"Unsupported declared charset: {name!r}")
        soup = BeautifulSoup(data.decode(encoding, errors="strict"), "html.parser")
        # Only the rendered body is extracted; legacy META headers can contain
        # unredacted party names and are intentionally not copied into metadata.
        for element in soup(["script", "style", "head", "meta", "link", "img"]):
            element.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for paragraph in soup.find_all("p"):
            paragraph.insert_after("\n\n")
        text = unescape(soup.get_text())
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return re.sub(r" +\n", "\n", text).strip()
