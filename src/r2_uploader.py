"""
S3/R2 Uploader for Ukrainian Legal Documents
Uploads markdown chunks to Cloudflare R2 (S3-compatible storage)
"""

import asyncio
import hashlib
import json
import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote as url_quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import R2Config, FolderStructure, load_config
from .markdown_converter import DocumentChunk

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    """Result of an upload operation"""
    success: bool
    key: str
    etag: Optional[str] = None
    error: Optional[str] = None
    size: int = 0


class R2Uploader:
    """Uploads documents to Cloudflare R2 (S3-compatible)"""
    
    def __init__(self, config: Optional[R2Config] = None):
        self.config = config or load_config().r2
        self.folders = FolderStructure()
        
        if not self.config.validate():
            raise ValueError(
                "R2 configuration incomplete. Set environment variables:\n"
                "  R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME"
            )
        
        self.client = self._create_client()
        self._ensure_bucket_exists()
    
    def _create_client(self):
        """Create S3 client configured for Cloudflare R2"""
        return boto3.client(
            's3',
            endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            region_name=self.config.region,
            config=Config(
                signature_version='s3v4',
                retries={'max_attempts': 3, 'mode': 'adaptive'}
            )
        )
    
    def _ensure_bucket_exists(self):
        """Check if bucket exists, create if not"""
        try:
            self.client.head_bucket(Bucket=self.config.bucket_name)
            logger.info(f"Bucket '{self.config.bucket_name}' exists")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == '404':
                logger.info(f"Creating bucket '{self.config.bucket_name}'")
                self.client.create_bucket(Bucket=self.config.bucket_name)
            else:
                raise
    
    def get_object_key(
        self,
        doc_id: str,
        chunk_id: str,
        doc_type: str = "laws"
    ) -> str:
        """
        Generate S3 object key for a chunk.
        
        Structure:
        - constitution/article_001.md
        - codes/{code_name}/article_001.md
        - laws/{year}/{doc_id}/chunk_001.md
        """
        # Clean up doc_id for filesystem
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        
        if doc_type == "constitution":
            return f"{self.folders.constitution}/{chunk_id}.md"
        elif doc_type == "code":
            return f"{self.folders.codes}/{safe_doc_id}/{chunk_id}.md"
        else:
            # For regular laws, organize by year if available
            return f"{self.folders.laws}/{safe_doc_id}/{chunk_id}.md"
    
    def get_processed_doc_ids(self) -> Set[str]:
        """
        Get set of already-processed document IDs by scanning R2 bucket.
        
        Returns safe_nreg values (with / replaced by _) for all documents
        that have metadata files uploaded.
        """
        processed = set()
        
        # Scan for _metadata.json files in laws folder
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(
                Bucket=self.config.bucket_name,
                Prefix=f"{self.folders.laws}/",
            ):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    # Look for _metadata.json files to identify complete documents
                    if key.endswith('/_metadata.json'):
                        # Extract doc_id from path: laws/{safe_doc_id}/_metadata.json
                        parts = key.split('/')
                        if len(parts) >= 2:
                            safe_doc_id = parts[1]
                            processed.add(safe_doc_id)
            
            logger.info(f"Found {len(processed)} already processed documents in R2")
            
        except ClientError as e:
            logger.warning(f"Failed to list existing documents: {e}")
        
        return processed
    
    def upload_chunk(
        self,
        chunk: DocumentChunk,
        doc_type: str = "laws"
    ) -> UploadResult:
        """Upload a single document chunk to R2"""
        key = self.get_object_key(chunk.doc_id, chunk.chunk_id, doc_type)
        content = chunk.to_markdown()
        
        try:
            # Calculate content hash for integrity
            content_bytes = content.encode('utf-8')
            content_md5 = hashlib.md5(content_bytes).hexdigest()
            
            # Upload with metadata (URL-encode non-ASCII values for S3 compatibility)
            response = self.client.put_object(
                Bucket=self.config.bucket_name,
                Key=key,
                Body=content_bytes,
                ContentType='text/markdown; charset=utf-8',
                Metadata={
                    'doc_id': url_quote(chunk.doc_id, safe=''),
                    'chunk_id': url_quote(chunk.chunk_id, safe=''),
                    'chunk_index': str(chunk.chunk_index),
                    'section_type': url_quote(chunk.section_type, safe=''),
                    'content_hash': chunk.content_hash,
                }
            )
            
            logger.debug(f"Uploaded: {key}")
            
            return UploadResult(
                success=True,
                key=key,
                etag=response.get('ETag', '').strip('"'),
                size=len(content_bytes)
            )
            
        except ClientError as e:
            error_msg = str(e)
            logger.error(f"Failed to upload {key}: {error_msg}")
            return UploadResult(
                success=False,
                key=key,
                error=error_msg
            )
    
    def upload_chunks(
        self,
        chunks: List[DocumentChunk],
        doc_type: str = "laws",
        max_workers: int = 5
    ) -> List[UploadResult]:
        """Upload multiple chunks in parallel"""
        results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self.upload_chunk, chunk, doc_type)
                for chunk in chunks
            ]
            
            for future in futures:
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Upload task failed: {e}")
                    results.append(UploadResult(
                        success=False,
                        key="unknown",
                        error=str(e)
                    ))
        
        # Log summary
        success_count = sum(1 for r in results if r.success)
        logger.info(f"Uploaded {success_count}/{len(chunks)} chunks")
        
        return results
    
    def upload_metadata(
        self,
        doc_id: str,
        metadata: Dict[str, Any],
        doc_type: str = "laws"
    ) -> UploadResult:
        """Upload document metadata as JSON"""
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        
        if doc_type == "constitution":
            key = f"{self.folders.constitution}/_metadata.json"
        else:
            key = f"{self.folders.laws}/{safe_doc_id}/_metadata.json"
        
        try:
            content = json.dumps(metadata, ensure_ascii=False, indent=2)
            content_bytes = content.encode('utf-8')
            
            self.client.put_object(
                Bucket=self.config.bucket_name,
                Key=key,
                Body=content_bytes,
                ContentType='application/json; charset=utf-8'
            )
            
            return UploadResult(
                success=True,
                key=key,
                size=len(content_bytes)
            )
            
        except ClientError as e:
            return UploadResult(
                success=False,
                key=key,
                error=str(e)
            )
    
    def upload_index(
        self,
        documents: List[Dict[str, Any]]
    ) -> UploadResult:
        """Upload a document index for quick lookups"""
        key = f"{self.folders.metadata}/document_index.json"
        
        try:
            content = json.dumps({
                'version': '1.0',
                'document_count': len(documents),
                'documents': documents
            }, ensure_ascii=False, indent=2)
            
            content_bytes = content.encode('utf-8')
            
            self.client.put_object(
                Bucket=self.config.bucket_name,
                Key=key,
                Body=content_bytes,
                ContentType='application/json; charset=utf-8'
            )
            
            return UploadResult(
                success=True,
                key=key,
                size=len(content_bytes)
            )
            
        except ClientError as e:
            return UploadResult(
                success=False,
                key=key,
                error=str(e)
            )
    
    def list_objects(
        self,
        prefix: str = "",
        max_keys: int = 1000
    ) -> List[Dict[str, Any]]:
        """List objects in bucket with prefix"""
        objects = []
        paginator = self.client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(
            Bucket=self.config.bucket_name,
            Prefix=prefix,
            PaginationConfig={'MaxItems': max_keys}
        ):
            for obj in page.get('Contents', []):
                objects.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'etag': obj['ETag'].strip('"')
                })
        
        return objects
    
    def get_object(self, key: str) -> Optional[bytes]:
        """Get object content by key"""
        try:
            response = self.client.get_object(
                Bucket=self.config.bucket_name,
                Key=key
            )
            return response['Body'].read()
        except ClientError:
            return None
    
    def delete_object(self, key: str) -> bool:
        """Delete an object by key"""
        try:
            self.client.delete_object(
                Bucket=self.config.bucket_name,
                Key=key
            )
            return True
        except ClientError:
            return False
    
    def object_exists(self, key: str) -> bool:
        """Check if an object exists"""
        try:
            self.client.head_object(
                Bucket=self.config.bucket_name,
                Key=key
            )
            return True
        except ClientError:
            return False
    
    def sync_document(
        self,
        chunks: List[DocumentChunk],
        doc_id: str,
        doc_type: str = "laws",
        force: bool = False
    ) -> Tuple[int, int, int]:
        """
        Sync document chunks to R2, skipping unchanged files.
        
        Returns: (uploaded, skipped, failed)
        """
        uploaded = 0
        skipped = 0
        failed = 0
        
        for chunk in chunks:
            key = self.get_object_key(chunk.doc_id, chunk.chunk_id, doc_type)
            
            if not force and self.object_exists(key):
                # Check if content changed
                existing = self.get_object(key)
                if existing:
                    existing_hash = hashlib.md5(existing).hexdigest()[:12]
                    if existing_hash == chunk.content_hash:
                        skipped += 1
                        continue
            
            result = self.upload_chunk(chunk, doc_type)
            if result.success:
                uploaded += 1
            else:
                failed += 1
        
        logger.info(
            f"Sync {doc_id}: {uploaded} uploaded, {skipped} skipped, {failed} failed"
        )
        
        return uploaded, skipped, failed


class LocalStorage:
    """
    Local storage for development/testing without R2.
    Mimics R2Uploader interface but saves to local filesystem.
    """
    
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.folders = FolderStructure()
    
    def get_object_key(
        self,
        doc_id: str,
        chunk_id: str,
        doc_type: str = "laws"
    ) -> str:
        """Generate file path for a chunk"""
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        
        if doc_type == "constitution":
            return f"{self.folders.constitution}/{chunk_id}.md"
        elif doc_type == "code":
            return f"{self.folders.codes}/{safe_doc_id}/{chunk_id}.md"
        else:
            return f"{self.folders.laws}/{safe_doc_id}/{chunk_id}.md"
    
    def upload_chunk(
        self,
        chunk: DocumentChunk,
        doc_type: str = "laws"
    ) -> UploadResult:
        """Save chunk to local file"""
        rel_path = self.get_object_key(chunk.doc_id, chunk.chunk_id, doc_type)
        file_path = self.output_dir / rel_path
        
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            content = chunk.to_markdown()
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return UploadResult(
                success=True,
                key=rel_path,
                size=len(content.encode('utf-8'))
            )
            
        except Exception as e:
            return UploadResult(
                success=False,
                key=rel_path,
                error=str(e)
            )
    
    def upload_chunks(
        self,
        chunks: List[DocumentChunk],
        doc_type: str = "laws",
        max_workers: int = 5
    ) -> List[UploadResult]:
        """Save multiple chunks to local files"""
        return [self.upload_chunk(chunk, doc_type) for chunk in chunks]
    
    def upload_metadata(
        self,
        doc_id: str,
        metadata: Dict[str, Any],
        doc_type: str = "laws"
    ) -> UploadResult:
        """Save metadata to local JSON file"""
        safe_doc_id = doc_id.replace("/", "_").replace("\\", "_")
        
        if doc_type == "constitution":
            rel_path = f"{self.folders.constitution}/_metadata.json"
        else:
            rel_path = f"{self.folders.laws}/{safe_doc_id}/_metadata.json"
        
        file_path = self.output_dir / rel_path
        
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            return UploadResult(
                success=True,
                key=rel_path,
                size=file_path.stat().st_size
            )
            
        except Exception as e:
            return UploadResult(
                success=False,
                key=rel_path,
                error=str(e)
            )
    
    def upload_index(self, index_data: Dict[str, Any]) -> UploadResult:
        """Save index to local JSON file"""
        rel_path = "index.json"
        file_path = self.output_dir / rel_path
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
            
            return UploadResult(
                success=True,
                key=rel_path,
                size=file_path.stat().st_size
            )
            
        except Exception as e:
            return UploadResult(
                success=False,
                key=rel_path,
                error=str(e)
            )
    
    def get_processed_doc_ids(self) -> Set[str]:
        """
        Get set of already-processed document IDs by scanning local folders.
        
        Returns safe_nreg values (with / replaced by _) for all documents
        that have metadata files saved locally.
        """
        processed = set()
        laws_dir = self.output_dir / self.folders.laws
        
        if laws_dir.exists():
            for doc_dir in laws_dir.iterdir():
                if doc_dir.is_dir():
                    metadata_file = doc_dir / "_metadata.json"
                    if metadata_file.exists():
                        processed.add(doc_dir.name)
        
        logger.info(f"Found {len(processed)} already processed documents locally")
        return processed


def get_uploader(use_local: bool = False, output_dir: str = "./output"):
    """
    Get appropriate uploader based on configuration.
    
    Falls back to local storage if R2 is not configured.
    """
    if use_local:
        logger.info("Using local storage")
        return LocalStorage(output_dir)
    
    try:
        config = load_config().r2
        if config.validate():
            logger.info("Using Cloudflare R2 storage")
            return R2Uploader(config)
    except Exception as e:
        logger.warning(f"R2 not available: {e}")
    
    logger.info("Falling back to local storage")
    return LocalStorage(output_dir)


def test_uploader():
    """Test the uploader with local storage"""
    logging.basicConfig(level=logging.INFO)
    
    # Create test chunk
    chunk = DocumentChunk(
        doc_id="test-doc",
        chunk_id="test-doc_0001",
        chunk_index=1,
        content="# Тестова стаття\n\nЦе тестовий контент українською мовою.",
        title="Тестовий документ",
        section_type="article",
        section_number="Стаття 1",
        section_title="Загальні положення",
        metadata={"test": True}
    )
    
    # Use local storage for testing
    uploader = LocalStorage("./test_output")
    result = uploader.upload_chunk(chunk, "laws")
    
    print(f"Upload result: {result}")
    print(f"File saved to: ./test_output/{result.key}")


if __name__ == "__main__":
    test_uploader()
