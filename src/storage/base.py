"""Shared storage contract, upload results, and source-aware object keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Set, runtime_checkable

from ..config import FolderStructure

if TYPE_CHECKING:
    from ..core.models import LegalDocument


@dataclass
class UploadResult:
    success: bool
    key: str
    etag: Optional[str] = None
    error: Optional[str] = None
    size: int = 0


@runtime_checkable
class Storage(Protocol):
    """Storage operations used by source pipelines."""

    def upload_document(self, document: LegalDocument, doc_type: str = "laws") -> UploadResult: ...

    def upload_metadata(self, doc_id: str, metadata: Dict[str, Any], doc_type: str = "laws") -> UploadResult: ...

    def get_processed_doc_ids(self) -> Set[str]: ...

    def upload_index(self, documents: List[Dict[str, Any]]) -> UploadResult: ...


def _validate_component(value: str, name: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (
        not value and not allow_empty
    ) or value in {".", ".."} or any(
        character in "/\\" or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError(f"{name} must be a safe single path component")


class _StorageKeys:
    folders = FolderStructure()

    def __init__(self, namespace: str = "") -> None:
        _validate_component(namespace, "namespace", allow_empty=True)
        self.namespace = namespace

    def _with_namespace(self, key: str) -> str:
        return f"{self.namespace}/{key}" if self.namespace else key

    @property
    def metadata_prefix(self) -> str:
        return self._with_namespace(f"{self.folders.metadata}/")

    def get_object_key(self, doc_id: str, doc_type: str = "laws") -> str:
        _validate_component(doc_type, "doc_type")
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        folder = {
            "constitution": self.folders.constitution,
            "code": self.folders.codes,
            "laws": self.folders.laws,
        }.get(doc_type, doc_type)
        return self._with_namespace(f"{folder}/{safe_doc_id}.md")

    def get_metadata_key(self, doc_id: str, doc_type: str = "laws") -> str:
        _validate_component(doc_type, "doc_type")
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        # Keep the legacy singular 'code' metadata directory.
        return f"{self.metadata_prefix}{doc_type}/{safe_doc_id}.json"

    def get_index_key(self) -> str:
        return f"{self.metadata_prefix}document_index.json"

    def _processed_doc_id(self, key: str) -> Optional[str]:
        """Recognize only this namespace's <doc_type>/<id>.json metadata."""
        if not key.startswith(self.metadata_prefix):
            return None
        parts = key[len(self.metadata_prefix):].split("/")
        # This also excludes the root index and metadata nested under other sources.
        if len(parts) != 2 or not parts[1].endswith(".json"):
            return None
        try:
            _validate_component(parts[0], "doc_type")
        except ValueError:
            return None
        return parts[1][:-5]
