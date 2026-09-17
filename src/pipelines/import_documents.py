"""Sequential, streaming ingestion for normalized export sources."""

from ..core.source import DocumentSource
from ..storage import Storage


class ImportPipeline:
    def __init__(self, source: DocumentSource, storage: Storage):
        self.source = source
        self.storage = storage

    def run(self, limit: int | None = None, skip_existing: bool = False) -> dict:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        stats = {"processed": 0, "skipped": 0, "errors": 0}
        if limit == 0:
            return stats
        existing = self.storage.get_processed_doc_ids() if skip_existing else set()
        documents = iter(self.source.iter_documents())
        try:
            count = 0
            while limit is None or count < limit:
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
        finally:
            close = getattr(documents, "close", None)
            if close:
                close()
        return stats
