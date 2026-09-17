"""
Configuration for Ukrainian Legal RAG Pipeline
Connects to data.rada.gov.ua and uploads to Cloudflare R2
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Load .env file if it exists
from dotenv import load_dotenv
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

@dataclass
class RadaAPIConfig:
    """Configuration for Verkhovna Rada Open Data API"""

    # Base URLs
    base_url: str = "https://data.rada.gov.ua"
    laws_base_url: str = "https://data.rada.gov.ua/laws"
    zakon_base_url: str = "https://zakon.rada.gov.ua/laws"

    # Open data files paths
    ogd_base_url: str = "https://data.rada.gov.ua/ogd"

    # API endpoints
    laws_show_endpoint: str = "/laws/show/{nreg}.json"  # Full document JSON
    laws_card_endpoint: str = "/laws/card/{nreg}.json"  # Document card JSON
    laws_text_endpoint: str = "/laws/show/{nreg}.txt"   # Plain text

    # List endpoints
    all_docs_list: str = "/laws/main/a.json"  # Paginated list of all documents
    recent_docs_list: str = "/laws/main/r.json"  # Recently updated
    primary_acts_list: str = "/ogd/zak/laws/data/csv/perv1.txt"  # Primary acts (non-international)
    primary_intl_list: str = "/ogd/zak/laws/data/csv/perv2.txt"  # International treaties
    inactive_acts_list: str = "/ogd/zak/laws/data/csv/perv0.txt"  # Inactive acts

    # Primary acts data
    primary_cards_json: str = "/ogd/zak/perv/card.json"  # All primary acts cards
    primary_texts_json: str = "/ogd/zak/perv/texts.json"  # Primary acts text info

    # User agent for API requests (use 'OpenData' for non-JSON or token for JSON)
    user_agent: str = "OpenData"
    user_agent_token: Optional[str] = None  # Set via env: RADA_API_TOKEN

    # Rate limiting (API limits: 60 req/min, 100k req/day)
    requests_per_minute: int = 10  # Conservative limit
    delay_between_requests: float = 6.0  # 5-7 seconds recommended

    def __post_init__(self):
        # Load token from environment if available
        self.user_agent_token = os.getenv("RADA_API_TOKEN", self.user_agent_token)


@dataclass
class R2Config:
    """Configuration for Cloudflare R2 (S3-compatible)"""

    # R2 endpoint (format: https://<account_id>.r2.cloudflarestorage.com)
    endpoint_url: str = field(default_factory=lambda: os.getenv("R2_ENDPOINT_URL", ""))

    # Credentials
    access_key_id: str = field(default_factory=lambda: os.getenv("R2_ACCESS_KEY_ID", ""))
    secret_access_key: str = field(default_factory=lambda: os.getenv("R2_SECRET_ACCESS_KEY", ""))

    # Bucket settings
    bucket_name: str = field(default_factory=lambda: os.getenv("R2_BUCKET_NAME", "ukrainian-legal-docs"))

    # Region (R2 uses 'auto' or 'wnam', 'enam', 'weur', 'eeur', 'apac')
    region: str = "auto"

    # Upload settings
    max_concurrent_uploads: int = 5

    def validate(self) -> bool:
        """Check if all required settings are present"""
        return all([
            self.endpoint_url,
            self.access_key_id,
            self.secret_access_key,
            self.bucket_name
        ])


@dataclass
class DocumentTypes:
    """Ukrainian legal document type codes"""

    # Priority document types for RAG
    priority_types: dict = field(default_factory=lambda: {
        "254к/96-вр": "Конституція України",
        # Кодекси (Codes) - correct nreg values
        "codes": [
            "435-15",      # Цивільний кодекс
            "436-15",      # Господарський кодекс
            "2341-14",     # Кримінальний кодекс
            "2947-14",     # Сімейний кодекс
            "2768-14",     # Земельний кодекс
            "2456-17",     # Бюджетний кодекс
            "2755-17",     # Податковий кодекс
            "4651-17",     # Кримінальний процесуальний кодекс
            "1618-15",     # Цивільний процесуальний кодекс
            "2747-15",     # Кодекс адміністративного судочинства
            "396-IX",      # Виборчий кодекс
        ],
        # Key laws by topic
        "key_laws": {
            "labor": ["322-08"],  # Кодекс законів про працю
            "land": ["2768-14"],  # Земельний кодекс
            "housing": ["5464-10"],  # Житловий кодекс
        }
    })


@dataclass
class FolderStructure:
    """Folder structure for R2 storage"""

    # Root folders
    constitution: str = "constitution"
    codes: str = "codes"
    laws: str = "laws"
    decrees: str = "decrees"
    resolutions: str = "resolutions"
    international: str = "international"

    # Metadata
    metadata: str = "_metadata"

    @staticmethod
    def get_folder_for_type(doc_type: int) -> str:
        """Get folder name based on document type ID"""
        type_mapping = {
            1: "laws",           # Закони
            2: "decrees",        # Укази
            3: "resolutions",    # Постанови
            4: "orders",         # Розпорядження
            5: "regulations",    # Положення
            6: "instructions",   # Інструкції
            7: "international",  # Міжнародні документи
        }
        return type_mapping.get(doc_type, "other")


@dataclass
class PipelineConfig:
    """Main pipeline configuration"""

    rada: RadaAPIConfig = field(default_factory=RadaAPIConfig)
    r2: R2Config = field(default_factory=R2Config)
    doc_types: DocumentTypes = field(default_factory=DocumentTypes)
    folders: FolderStructure = field(default_factory=FolderStructure)

    # Processing settings
    process_constitution_first: bool = True
    process_codes: bool = True
    process_active_laws_only: bool = True

    # Local cache directory
    cache_dir: str = "./cache"
    output_dir: str = "./output"

    # Logging
    log_level: str = "INFO"
    log_file: str = "pipeline.log"


# Key document identifiers
CONSTITUTION_NREG = "254к/96-вр"

# Document status codes
DOC_STATUS_ACTIVE = 0  # Діючий (active)
DOC_STATUS_INACTIVE = 1  # Втратив чинність (inactive)

# Document type codes (from API docs)
DOC_TYPES = {
    1: "Закон",
    2: "Кодекс",
    3: "Декрет",
    4: "Постанова",
    5: "Указ",
    6: "Розпорядження",
    7: "Наказ",
    8: "Рішення",
    9: "Положення",
    10: "Інструкція",
    11: "Правила",
    12: "Договір",
    13: "Угода",
    14: "Конвенція",
    15: "Протокол",
}


def load_config() -> PipelineConfig:
    """Load configuration from environment and defaults"""
    return PipelineConfig()


if __name__ == "__main__":
    # Test configuration
    config = load_config()
    print(f"Rada API Base URL: {config.rada.base_url}")
    print(f"R2 Configured: {config.r2.validate()}")
    print(f"Cache Directory: {config.cache_dir}")
