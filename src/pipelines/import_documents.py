"""Sequential, streaming ingestion for normalized export sources."""

import logging
import time

from ..core.source import DocumentSource
from ..storage import Storage

logger = logging.getLogger(__name__)


class ImportPipeline:
    def __init__(self, source: DocumentSource, storage: Storage):
        self.source = source
        self.storage = storage
        self.stats = {"processed": 0, "skipped": 0, "errors": 0}

    def run(self, limit: int | None = None, skip_existing: bool = False,
            *, existing_ids: set[str] | None = None) -> dict:
        """Optionally reuse preloaded IDs; stats remain available after interruption."""
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        stats = self.stats = {"processed": 0, "skipped": 0, "errors": 0}
        if limit == 0:
            return stats
        started = time.monotonic()
        existing = set()
        if skip_existing:
            existing = existing_ids if existing_ids is not None else self.storage.get_processed_doc_ids()
        documents = iter(self.source.iter_documents())
        document = None
        try:
            count = 0
            while limit is None or count < limit:
                document = None
                try:
                    document = next(documents)
                except StopIteration:
                    break
                count += 1
                if document.doc_id in existing:
                    stats["skipped"] += 1
                    continue
                result = self.storage.upload_document(document, self.source.document_type)
                if result.success:
                    result = self.storage.upload_metadata(
                        document.doc_id,
                        {**document.metadata, "title": document.title, "content_hash": document.content_hash},
                        self.source.document_type,
                    )
                if result.success:
                    stats["processed"] += 1
                    if skip_existing:
                        existing.add(document.doc_id)
                else:
                    stats["errors"] += 1
                elapsed = time.monotonic() - started
                logger.log(logging.INFO if result.success else logging.ERROR,
                           "%s id=%s url=%s processed=%d skipped=%d errors=%d elapsed=%.1fs rate=%.2f docs/s%s",
                           "Saved" if result.success else "Save failed", document.doc_id,
                           document.metadata.get("doc_url", document.metadata.get("source_url", "unknown")),
                           stats["processed"], stats["skipped"], stats["errors"], elapsed,
                           stats["processed"] / elapsed if elapsed > 0 else 0,
                           "" if result.success else f" error={result.error or 'storage write failed'}")
        except Exception:
            stats["errors"] += 1
            if document is not None:
                logger.error("Save interrupted id=%s url=%s", document.doc_id,
                             document.metadata.get("doc_url", document.metadata.get("source_url", "unknown")))
            raise
        finally:
            close = getattr(documents, "close", None)
            if close:
                close()
        return stats
