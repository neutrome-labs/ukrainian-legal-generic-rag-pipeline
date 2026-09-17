"""Local filesystem storage for complete Markdown source documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Set

from .base import UploadResult, _StorageKeys

if TYPE_CHECKING:
    from ..core.models import LegalDocument


class LocalStorage(_StorageKeys):
    """Local equivalent of R2Uploader for tests and development."""

    def __init__(self, output_dir: str = "./output", namespace: str = ""):
        super().__init__(namespace)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_processed_doc_ids(self) -> Set[str]:
        metadata_root = self.output_dir / self.metadata_prefix
        processed = set()
        for path in metadata_root.glob("*/*.json"):
            if path.is_file():
                doc_id = self._processed_doc_id(path.relative_to(self.output_dir).as_posix())
                if doc_id is not None:
                    processed.add(doc_id)
        return processed

    def _write(self, key: str, content: bytes) -> UploadResult:
        path = self.output_dir / key
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return UploadResult(True, key, size=len(content))
        except OSError as error:
            return UploadResult(False, key, error=str(error))

    def upload_document(self, document: LegalDocument, doc_type: str = "laws") -> UploadResult:
        key = self.get_object_key(document.doc_id, doc_type)
        return self._write(key, document.to_markdown().encode("utf-8"))

    def upload_metadata(self, doc_id: str, metadata: Dict[str, Any], doc_type: str = "laws") -> UploadResult:
        key = self.get_metadata_key(doc_id, doc_type)
        content = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        return self._write(key, content)

    def upload_index(self, documents: List[Dict[str, Any]]) -> UploadResult:
        key = self.get_index_key()
        content = json.dumps({"version": "2.0", "document_count": len(documents), "documents": documents}, ensure_ascii=False, indent=2).encode("utf-8")
        return self._write(key, content)
