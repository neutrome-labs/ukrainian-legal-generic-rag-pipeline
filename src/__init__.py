"""
Ukrainian Legal Documents RAG Pipeline - Core Modules
"""

from .config import (
    CONSTITUTION_NREG,
    DOC_STATUS_ACTIVE,
    PipelineConfig,
    R2Config,
    RadaAPIConfig,
    ChunkingConfig,
    FolderStructure,
    load_config,
)

from .rada_api_client import RadaAPIClient, LawDocument

from .markdown_converter import (
    MarkdownConverter,
    ConstitutionProcessor,
    DocumentChunk,
)

from .r2_uploader import (
    R2Uploader,
    LocalStorage,
    UploadResult,
    get_uploader,
)

__all__ = [
    # Config
    'CONSTITUTION_NREG',
    'DOC_STATUS_ACTIVE',
    'PipelineConfig',
    'R2Config',
    'RadaAPIConfig',
    'ChunkingConfig',
    'FolderStructure',
    'load_config',
    # API Client
    'RadaAPIClient',
    'LawDocument',
    # Markdown Converter
    'MarkdownConverter',
    'ConstitutionProcessor',
    'DocumentChunk',
    # R2 Uploader
    'R2Uploader',
    'LocalStorage',
    'UploadResult',
    'get_uploader',
]
