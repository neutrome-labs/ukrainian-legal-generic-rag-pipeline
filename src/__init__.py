"""Ukrainian Legal RAG Pipeline - public API."""

from .config import (
    CONSTITUTION_NREG,
    DOC_STATUS_ACTIVE,
    FolderStructure,
    PipelineConfig,
    R2Config,
    RadaAPIConfig,
    load_config,
)
from .core.models import LegalDocument
from .sources.rada.client import LawDocument, RadaAPIClient, RateLimiter
from .sources.rada.converter import MarkdownConverter
from .storage import LocalStorage, R2Uploader, Storage, UploadResult, get_uploader

__all__ = [
    # Config
    'CONSTITUTION_NREG',
    'DOC_STATUS_ACTIVE',
    'FolderStructure',
    'PipelineConfig',
    'R2Config',
    'RadaAPIConfig',
    'load_config',
    # Shared model
    'LegalDocument',
    # Rada source
    'RadaAPIClient',
    'LawDocument',
    'RateLimiter',
    'MarkdownConverter',
    # Storage
    'Storage',
    'UploadResult',
    'LocalStorage',
    'R2Uploader',
    'get_uploader',
]
