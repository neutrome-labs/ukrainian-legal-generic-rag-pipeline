"""
Rada Open Data API Client
Fetches Ukrainian legislation from data.rada.gov.ua
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urljoin, quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import PipelineConfig, RadaAPIConfig, load_config

logger = logging.getLogger(__name__)


@dataclass
class LawDocument:
    """Represents a Ukrainian legal document"""
    dokid: int                      # Unique document ID
    nreg: str                       # System registration number
    nazva: str                      # Document title
    status: int                     # Document status code (see STATUS_CODES)
    types: List[int]                # Document type IDs
    date_adopted: Optional[str]     # Adoption date
    date_effective: Optional[str]   # Effective date
    date_current_edition: Optional[str]  # Current edition date
    organs: List[Dict]              # Issuing bodies
    text: Optional[str] = None      # Document text (plain or HTML)
    structure: Optional[Dict] = None  # Document structure
    card: Optional[Dict] = None     # Full card data

    # Status codes from the API:
    # 0 = Not defined, 1 = Lost effect (inactive), 2 = Coming into effect
    # 3 = Effect suspended, 4 = Effect restored, 5 = Active (Чинний)
    # 6 = Not yet in effect, 7 = Not applied in Ukraine territory
    ACTIVE_STATUSES = {2, 4, 5}  # Statuses that mean the document is active

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def status_name(self) -> str:
        """Get human-readable status name"""
        status_names = {
            0: "Не визначено",
            1: "Втратив чинність",
            2: "Набирає чинності",
            3: "Дію зупинено",
            4: "Дію відновлено",
            5: "Чинний",
            6: "Не набрав чинності",
            7: "Не застосовується на території України"
        }
        return status_names.get(self.status, f"Unknown ({self.status})")

    @property
    def safe_nreg(self) -> str:
        """Get filesystem-safe registration number"""
        return self.nreg.replace("/", "_").replace("\\", "_")


class RateLimiter:
    """Simple rate limiter for API requests"""

    def __init__(self, delay: float = 6.0):
        self.delay = delay
        self.last_request_time = 0.0

    def wait(self):
        """Wait if needed to respect rate limit"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.delay:
            sleep_time = self.delay - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        self.last_request_time = time.time()


class RadaAPIClient:
    """Client for Verkhovna Rada Open Data API"""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or load_config()
        self.api_config = self.config.rada
        self.rate_limiter = RateLimiter(self.api_config.delay_between_requests)

        # Set up session with retries
        self.session = self._create_session()

        # Local cache
        self.cache_dir = Path(self.config.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _create_session(self) -> requests.Session:
        """Create HTTP session with retry logic"""
        session = requests.Session()

        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            backoff_factor=1
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    def _get_headers(self, use_json: bool = False) -> Dict[str, str]:
        """Get request headers with appropriate User-Agent"""
        if use_json and self.api_config.user_agent_token:
            user_agent = self.api_config.user_agent_token
        else:
            user_agent = self.api_config.user_agent

        return {
            "User-Agent": user_agent,
            "Accept": "application/json" if use_json else "*/*",
        }

    def _make_request(
        self,
        url: str,
        use_json: bool = False,
        stream: bool = False
    ) -> requests.Response:
        """Make rate-limited request to API"""
        self.rate_limiter.wait()

        headers = self._get_headers(use_json)
        logger.debug(f"Fetching: {url}")

        response = self.session.get(url, headers=headers, stream=stream, timeout=30)
        response.raise_for_status()

        return response

    def get_primary_acts_list(self, include_international: bool = False) -> List[str]:
        """
        Get list of primary legislative act registration numbers.

        Primary acts are standalone laws that can be amended (not amendment-only laws).

        Note: The API returns CSV files in CP1251 encoding, so we decode manually.
        """
        nregs = []

        # Main primary acts (excluding international treaties)
        url = urljoin(self.api_config.base_url, self.api_config.primary_acts_list)
        try:
            response = self._make_request(url)
            # API returns CP1251-encoded text, decode properly to UTF-8
            text = response.content.decode('cp1251')
            nregs.extend(text.strip().split('\n'))
            logger.info(f"Loaded {len(nregs)} primary acts (domestic)")
        except Exception as e:
            logger.error(f"Failed to load primary acts list: {e}")

        if include_international:
            # International treaties
            url = urljoin(self.api_config.base_url, self.api_config.primary_intl_list)
            try:
                response = self._make_request(url)
                # API returns CP1251-encoded text, decode properly to UTF-8
                text = response.content.decode('cp1251')
                intl_nregs = text.strip().split('\n')
                nregs.extend(intl_nregs)
                logger.info(f"Loaded {len(intl_nregs)} international treaties")
            except Exception as e:
                logger.error(f"Failed to load international treaties list: {e}")

        return nregs

    def get_inactive_acts_list(self) -> List[str]:
        """Get list of inactive/repealed act registration numbers"""
        url = urljoin(self.api_config.base_url, self.api_config.inactive_acts_list)
        try:
            response = self._make_request(url)
            nregs = response.text.strip().split('\n')
            logger.info(f"Loaded {len(nregs)} inactive acts")
            return nregs
        except Exception as e:
            logger.error(f"Failed to load inactive acts list: {e}")
            return []

    def get_primary_acts_cards(self) -> List[Dict[str, Any]]:
        """Get all primary acts cards (metadata) in one request"""
        cache_file = self.cache_dir / "primary_cards.json"

        # Try cache first
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"Loaded {len(data)} cards from cache")
                return data
            except Exception as e:
                logger.warning(f"Cache read failed: {e}")

        # Fetch from API
        url = urljoin(self.api_config.base_url, self.api_config.primary_cards_json)
        try:
            response = self._make_request(url, use_json=True)
            data = response.json()

            # Cache the result
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"Fetched and cached {len(data)} primary act cards")
            return data
        except Exception as e:
            logger.error(f"Failed to fetch primary acts cards: {e}")
            return []

    def get_document_card(self, nreg: str) -> Optional[Dict[str, Any]]:
        """Get document card (metadata) by registration number"""
        # URL encode the nreg (handles Cyrillic), keep '/' unencoded as API expects it
        encoded_nreg = quote(nreg, safe='/')
        url = urljoin(
            self.api_config.base_url,
            self.api_config.laws_card_endpoint.format(nreg=encoded_nreg)
        )

        try:
            response = self._make_request(url, use_json=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Document not found: {nreg}")
            else:
                logger.error(f"Failed to fetch card for {nreg}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch card for {nreg}: {e}")
            return None

    def get_document_full(self, nreg: str) -> Optional[Dict[str, Any]]:
        """Get full document data including structure and text"""
        # URL encode the nreg (handles Cyrillic), keep '/' unencoded as API expects it
        encoded_nreg = quote(nreg, safe='/')
        url = urljoin(
            self.api_config.base_url,
            self.api_config.laws_show_endpoint.format(nreg=encoded_nreg)
        )

        try:
            response = self._make_request(url, use_json=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Document not found: {nreg}")
            else:
                logger.error(f"Failed to fetch document {nreg}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch document {nreg}: {e}")
            return None

    def get_document_text(self, nreg: str) -> Optional[str]:
        """Get plain text of document"""
        # URL encode the nreg (handles Cyrillic), keep '/' unencoded as API expects it
        encoded_nreg = quote(nreg, safe='/')
        url = urljoin(
            self.api_config.base_url,
            self.api_config.laws_text_endpoint.format(nreg=encoded_nreg)
        )

        try:
            response = self._make_request(url)
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch text for {nreg}: {e}")
            return None

    def get_recent_documents(self, page: int = 1) -> List[Dict[str, Any]]:
        """Get list of recently updated documents"""
        url = urljoin(
            self.api_config.base_url,
            f"/laws/main/r/page{page}.json"
        )

        try:
            response = self._make_request(url, use_json=True)
            data = response.json()
            return data.get('docs', [])
        except Exception as e:
            logger.error(f"Failed to fetch recent documents: {e}")
            return []

    def parse_document(self, data: Dict[str, Any]) -> Optional[LawDocument]:
        """Parse API response into LawDocument object"""
        try:
            doc = data.get('doc', data)

            return LawDocument(
                dokid=doc.get('dokid', 0),
                nreg=doc.get('nreg', ''),
                nazva=doc.get('nazva', ''),
                status=doc.get('status', {}).get('status', 0) if isinstance(doc.get('status'), dict) else doc.get('status', 0),
                types=doc.get('types', []),
                date_adopted=doc.get('datpub'),
                date_effective=doc.get('datchyn'),
                date_current_edition=doc.get('datred'),
                organs=[doc.get('organs', {})] if isinstance(doc.get('organs'), dict) else doc.get('organs', []),
                text=data.get('text'),
                structure=data.get('stru'),
                card=doc
            )
        except Exception as e:
            logger.error(f"Failed to parse document: {e}")
            return None

    def iterate_active_primary_acts(
        self,
        include_international: bool = False,
        skip_inactive: bool = True
    ) -> Iterator[LawDocument]:
        """
        Iterate over all active primary legislative acts.
        Yields LawDocument objects with full data.
        """
        # Get the list of primary act nregs
        nregs = self.get_primary_acts_list(include_international)
        inactive_nregs = set(self.get_inactive_acts_list()) if skip_inactive else set()

        logger.info(f"Processing {len(nregs)} primary acts...")

        for i, nreg in enumerate(nregs):
            nreg = nreg.strip()
            if not nreg:
                continue

            if skip_inactive and nreg in inactive_nregs:
                logger.debug(f"Skipping inactive: {nreg}")
                continue

            logger.info(f"[{i+1}/{len(nregs)}] Fetching: {nreg}")

            # Get full document data
            doc_data = self.get_document_full(nreg)
            if not doc_data:
                continue

            doc = self.parse_document(doc_data)
            if doc and (not skip_inactive or doc.is_active):
                yield doc

    def get_constitution(self) -> Optional[LawDocument]:
        """Get the Constitution of Ukraine"""
        from .config import CONSTITUTION_NREG

        logger.info("Fetching Constitution of Ukraine...")
        doc_data = self.get_document_full(CONSTITUTION_NREG)
        if doc_data:
            return self.parse_document(doc_data)
        return None

    def search_documents(
        self,
        query: str,
        doc_types: Optional[List[int]] = None,
        status: Optional[int] = None,
        page: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Search for documents (basic search).
        For more advanced queries, use the website directly.
        """
        # The API has limited search capabilities
        # For full-text search, the web interface is better
        params = {
            'text': query,
            'page': page
        }
        if doc_types:
            params['types'] = ','.join(map(str, doc_types))
        if status is not None:
            params['status'] = status

        url = urljoin(self.api_config.zakon_base_url, '/main/a.json')

        try:
            response = self._make_request(url, use_json=True)
            return response.json().get('docs', [])
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []


def test_client():
    """Test the API client"""
    logging.basicConfig(level=logging.INFO)

    client = RadaAPIClient()

    # Test getting Constitution
    print("\n=== Testing Constitution fetch ===")
    constitution = client.get_constitution()
    if constitution:
        print(f"Title: {constitution.nazva}")
        print(f"NREG: {constitution.nreg}")
        print(f"Active: {constitution.is_active}")
        print(f"Has structure: {constitution.structure is not None}")

    # Test getting primary acts list
    print("\n=== Testing primary acts list ===")
    nregs = client.get_primary_acts_list()
    print(f"Found {len(nregs)} primary acts")
    if nregs:
        print(f"First 5: {nregs[:5]}")

    # Test getting a specific document
    print("\n=== Testing document fetch ===")
    if nregs:
        test_nreg = nregs[0]
        doc = client.get_document_full(test_nreg)
        if doc:
            parsed = client.parse_document(doc)
            if parsed:
                print(f"Title: {parsed.nazva}")


if __name__ == "__main__":
    test_client()
