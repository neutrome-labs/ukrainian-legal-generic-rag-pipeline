"""Storage adapters for complete Markdown source documents."""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote as url_quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import FolderStructure, R2Config, load_config
from .markdown_converter import LegalDocument

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    success: bool
    key: str
    etag: Optional[str] = None
    error: Optional[str] = None
    size: int = 0


class _StorageKeys:
    folders = FolderStructure()

    def get_object_key(self, doc_id: str, doc_type: str = "laws") -> str:
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        if doc_type == "constitution":
            return f"{self.folders.constitution}/{safe_doc_id}.md"
        if doc_type == "code":
            return f"{self.folders.codes}/{safe_doc_id}.md"
        return f"{self.folders.laws}/{safe_doc_id}.md"

    def get_metadata_key(self, doc_id: str, doc_type: str = "laws") -> str:
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        return f"{self.folders.metadata}/{doc_type}/{safe_doc_id}.json"


class R2Uploader(_StorageKeys):
    """Uploads one complete source document per R2 object."""

    def __init__(self, config: Optional[R2Config] = None):
        self.config = config or load_config().r2
        if not self.config.validate():
            raise ValueError("R2 configuration incomplete. Set R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME.")
        self.client = boto3.client(
            "s3", endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key_id, aws_secret_access_key=self.config.secret_access_key,
            region_name=self.config.region, config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "adaptive"}),
        )
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.config.bucket_name)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "404":
                self.client.create_bucket(Bucket=self.config.bucket_name)
            else:
                raise

    def get_processed_doc_ids(self) -> Set[str]:
        processed = set()
        paginator = self.client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.config.bucket_name, Prefix=f"{self.folders.metadata}/"):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(".json"):
                        processed.add(Path(obj["Key"]).stem)
        except ClientError as error:
            logger.warning("Failed to list processed documents: %s", error)
        return processed

    def upload_document(self, document: LegalDocument, doc_type: str = "laws") -> UploadResult:
        key = self.get_object_key(document.doc_id, doc_type)
        content = document.to_markdown().encode("utf-8")
        try:
            response = self.client.put_object(
                Bucket=self.config.bucket_name, Key=key, Body=content, ContentType="text/markdown; charset=utf-8",
                Metadata={"doc_id": url_quote(document.doc_id, safe=""), "content_hash": document.content_hash},
            )
            return UploadResult(True, key, response.get("ETag", "").strip('"'), size=len(content))
        except ClientError as error:
            logger.error("Failed to upload %s: %s", key, error)
            return UploadResult(False, key, error=str(error))

    def upload_metadata(self, doc_id: str, metadata: Dict[str, Any], doc_type: str = "laws") -> UploadResult:
        key = self.get_metadata_key(doc_id, doc_type)
        content = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self.client.put_object(Bucket=self.config.bucket_name, Key=key, Body=content, ContentType="application/json; charset=utf-8")
            return UploadResult(True, key, size=len(content))
        except ClientError as error:
            return UploadResult(False, key, error=str(error))

    def upload_index(self, documents: List[Dict[str, Any]]) -> UploadResult:
        key = f"{self.folders.metadata}/document_index.json"
        content = json.dumps({"version": "2.0", "document_count": len(documents), "documents": documents}, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self.client.put_object(Bucket=self.config.bucket_name, Key=key, Body=content, ContentType="application/json; charset=utf-8")
            return UploadResult(True, key, size=len(content))
        except ClientError as error:
            return UploadResult(False, key, error=str(error))


class LocalStorage(_StorageKeys):
    """Local equivalent of R2Uploader for tests and development."""

    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_processed_doc_ids(self) -> Set[str]:
        metadata_root = self.output_dir / self.folders.metadata
        return {path.stem for path in metadata_root.rglob("*.json")} if metadata_root.exists() else set()

    def upload_document(self, document: LegalDocument, doc_type: str = "laws") -> UploadResult:
        key = self.get_object_key(document.doc_id, doc_type)
        path = self.output_dir / key
        content = document.to_markdown().encode("utf-8")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return UploadResult(True, key, size=len(content))
        except OSError as error:
            return UploadResult(False, key, error=str(error))

    def upload_metadata(self, doc_id: str, metadata: Dict[str, Any], doc_type: str = "laws") -> UploadResult:
        key = self.get_metadata_key(doc_id, doc_type)
        path = self.output_dir / key
        content = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return UploadResult(True, key, size=len(content))
        except OSError as error:
            return UploadResult(False, key, error=str(error))

    def upload_index(self, documents: List[Dict[str, Any]]) -> UploadResult:
        key = f"{self.folders.metadata}/document_index.json"
        path = self.output_dir / key
        content = json.dumps({"version": "2.0", "document_count": len(documents), "documents": documents}, ensure_ascii=False, indent=2).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return UploadResult(True, key, size=len(content))


def get_uploader(use_local: bool = False, output_dir: str = "./output") -> R2Uploader | LocalStorage:
    return LocalStorage(output_dir) if use_local else R2Uploader()
