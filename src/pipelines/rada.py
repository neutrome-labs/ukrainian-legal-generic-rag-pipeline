"""
Ukrainian Legal Documents RAG Pipeline
Main orchestrator for fetching, processing, and uploading legal documents
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any, Dict, Optional, Set

from src.config import (
    CONSTITUTION_NREG,
    PipelineConfig,
    load_config,
)
from src.sources.rada.client import RadaAPIClient
from src.sources.rada.converter import MarkdownConverter
from src.core.models import LegalDocument
from src.storage import get_uploader, UploadResult

logger = logging.getLogger(__name__)


class LegalDocumentPipeline:
    """
    Main pipeline for processing Ukrainian legal documents.

    Workflow:
    1. Fetch document list from Rada Open Data
    2. Download document text and metadata
    3. Convert each source document to Markdown
    4. Upload to Cloudflare R2
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        use_local_storage: bool = False,
        skip_existing: bool = False
    ):
        self.config = config or load_config()
        self.api_client = RadaAPIClient(self.config)
        self.converter = MarkdownConverter()
        self.skip_existing = skip_existing

        # Initialize storage
        self.uploader = get_uploader(
            use_local=use_local_storage,
            output_dir=self.config.output_dir,
            config=self.config.r2
        )

        # Track processed documents (using safe_nreg format with / replaced by _)
        self.processed_docs: Set[str] = set()
        self._docs_lock = Lock()  # Lock for thread-safe access to processed_docs

        # Load already processed docs from storage if skip_existing is enabled
        if skip_existing:
            logger.info("Loading already processed documents from storage...")
            self.processed_docs = self.uploader.get_processed_doc_ids()
            logger.info(f"Will skip {len(self.processed_docs)} already processed documents")

        self.stats = {
            'documents_processed': 0,
            'documents_uploaded': 0,
            'errors': 0,
            'skipped': 0
        }
        self._stats_lock = Lock()  # Lock for thread-safe stats updates

    def process_constitution(self) -> Optional[LegalDocument]:
        """
        Process the Constitution of Ukraine with special handling.
        Uploads the complete Constitution as one Markdown document.
        """
        logger.info("=" * 60)
        logger.info("Processing Constitution of Ukraine (Конституція України)")
        logger.info("=" * 60)

        # Fetch the constitution
        constitution = self.api_client.get_constitution()
        if not constitution:
            logger.error("Failed to fetch Constitution")
            self.stats['errors'] += 1
            return None

        logger.info(f"Title: {constitution.nazva}")
        logger.info(f"Status: {constitution.status_name} (code: {constitution.status})")

        # Get the text
        text = self.api_client.get_document_text(CONSTITUTION_NREG)
        if not text:
            logger.error("Failed to fetch Constitution text")
            self.stats['errors'] += 1
            return None

        document = self.converter.process_document(
            doc_id=constitution.safe_nreg,
            title=constitution.nazva,
            text=text,
            structure=constitution.structure,
            metadata={"doc_type": "constitution", "adopted": constitution.date_adopted, "importance": "fundamental"},
        )
        # Upload metadata only after the document upload succeeds.
        metadata = {
            'nreg': constitution.nreg,
            'title': constitution.nazva,
            'adopted': constitution.date_adopted,
            'current_edition': constitution.date_current_edition,
            'processed_at': datetime.now().isoformat(),
            'source': 'data.rada.gov.ua'
        }
        if not self._upload_document_and_metadata(
            document, constitution.nreg, metadata, "constitution"
        ):
            return None

        return document

    def process_document(
        self,
        nreg: str,
        doc_type: str = "laws"
    ) -> Optional[LegalDocument]:
        """
        Process a single legal document. Thread-safe.
        """
        # Use safe_nreg format for consistency with storage keys
        safe_nreg = nreg.replace("/", "_").replace("\\", "_")

        # Thread-safe check for already processed
        with self._docs_lock:
            if safe_nreg in self.processed_docs:
                logger.debug(f"Skipping already processed: {nreg}")
                with self._stats_lock:
                    self.stats['skipped'] += 1
                return None

        # Fetch full document
        doc_data = self.api_client.get_document_full(nreg)
        if not doc_data:
            logger.warning(f"Could not fetch document: {nreg}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return None

        doc = self.api_client.parse_document(doc_data)
        if not doc:
            logger.warning(f"Could not parse document: {nreg}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return None

        # Skip inactive documents if configured
        if self.config.process_active_laws_only and not doc.is_active:
            logger.debug(f"Skipping inactive document: {nreg}")
            return None

        # Get plain text
        text = self.api_client.get_document_text(nreg)
        if not text:
            # Try to extract from structure
            if doc.structure:
                logger.debug("Extracting text from structure")
                text = self._extract_text_from_structure(doc.structure)

            if not text:
                logger.warning(f"No text available for: {nreg}")
                with self._stats_lock:
                    self.stats['errors'] += 1
                return None

        # Create metadata
        metadata = {
            'nreg': doc.nreg,
            'dokid': doc.dokid,
            'status': 'active' if doc.is_active else 'inactive',
            'date_adopted': doc.date_adopted,
            'date_current_edition': doc.date_current_edition,
            'types': doc.types,
        }

        document = self.converter.process_document(
            doc_id=doc.safe_nreg,
            title=doc.nazva,
            text=text,
            structure=doc.structure,
            metadata=metadata
        )

        if not document.content:
            logger.warning(f"No document content created for: {nreg}")
            return None

        # Upload the document and its metadata as one processing operation.
        full_metadata = {
            **metadata,
            'title': doc.nazva,
            'processed_at': datetime.now().isoformat(),
            'source': 'data.rada.gov.ua'
        }
        if not self._upload_document_and_metadata(
            document, doc.safe_nreg, full_metadata, doc_type
        ):
            return None

        return document

    def _upload_document_and_metadata(
        self,
        document: LegalDocument,
        doc_id: str,
        metadata: Dict[str, Any],
        doc_type: str
    ) -> bool:
        """Mark a document processed only after both storage writes succeed."""
        try:
            result = self.uploader.upload_document(document, doc_type=doc_type)
            if not result.success:
                logger.error("Document upload failed for %s: %s", doc_id, result.error)
                with self._stats_lock:
                    self.stats['errors'] += 1
                return False

            with self._stats_lock:
                self.stats['documents_uploaded'] += 1

            result = self.uploader.upload_metadata(doc_id, metadata, doc_type)
            if not result.success:
                logger.error("Metadata upload failed for %s: %s", doc_id, result.error)
                with self._stats_lock:
                    self.stats['errors'] += 1
                return False
        except Exception as error:
            logger.error("Upload failed for %s: %s", doc_id, error, exc_info=True)
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

        with self._stats_lock:
            self.stats['documents_processed'] += 1
        with self._docs_lock:
            self.processed_docs.add(doc_id.replace("/", "_").replace("\\", "_"))
        return True

    def _extract_text_from_structure(self, structure: Any) -> str:
        """Extract plain text from document structure"""
        if not structure:
            return ""

        texts = []

        if isinstance(structure, list):
            for item in structure:
                if isinstance(item, dict):
                    text = item.get('text', '')
                    if text:
                        texts.append(self.converter.html_to_text(text))
        elif isinstance(structure, dict):
            text = structure.get('text', '')
            if text:
                texts.append(self.converter.html_to_text(text))

        return '\n\n'.join(texts)

    def process_codes(self) -> Dict[str, LegalDocument]:
        """
        Process major Ukrainian codes (Кодекси).
        """
        logger.info("=" * 60)
        logger.info("Processing Ukrainian Codes (Кодекси)")
        logger.info("=" * 60)

        results = {}
        code_nregs = self.config.doc_types.priority_types.get('codes', [])

        for nreg in code_nregs:
            try:
                document = self.process_document(nreg, doc_type="code")
                if document:
                    results[nreg] = document
            except Exception as e:
                logger.error(f"Error processing code {nreg}: {e}")
                self.stats['errors'] += 1

        logger.info(f"Processed {len(results)} codes")
        return results

    def process_primary_acts(
        self,
        include_international: bool = False,
        limit: Optional[int] = None,
        max_workers: int = 4
    ) -> int:
        """
        Process all primary legislative acts with multithreading.

        Args:
            include_international: Include international treaties
            limit: Maximum number of documents to process (for testing)
            max_workers: Number of parallel download threads (default: 4)

        Returns:
            Number of documents processed
        """
        logger.info("=" * 60)
        logger.info("Processing Primary Legislative Acts")
        logger.info("=" * 60)

        if limit == 0:
            return 0

        # Get list of primary acts
        nregs = self.api_client.get_primary_acts_list(include_international)
        inactive = set(self.api_client.get_inactive_acts_list())

        # Filter out inactive if configured
        if self.config.process_active_laws_only:
            nregs = [n for n in nregs if n.strip() not in inactive]

        if limit is not None:
            nregs = nregs[:limit]

        # Clean up nregs and filter out constitution
        nregs = [n.strip() for n in nregs if n.strip() and n.strip() != CONSTITUTION_NREG]

        total = len(nregs)
        logger.info(f"Processing {total} primary acts with {max_workers} threads")

        processed = 0
        progress_lock = Lock()
        completed = [0]  # Use list to allow mutation in closure

        def process_single(nreg: str) -> bool:
            """Process a single document. Returns True if successful."""
            nonlocal completed
            try:
                document = self.process_document(nreg, doc_type="laws")
                with progress_lock:
                    completed[0] += 1
                    if document:
                        logger.info(f"[{completed[0]}/{total}] ✓ {nreg}")
                        return True
                    else:
                        logger.debug(f"[{completed[0]}/{total}] - {nreg} (skipped/no document)")
                        return False
            except Exception as e:
                with progress_lock:
                    completed[0] += 1
                    self.stats['errors'] += 1
                logger.error(f"[{completed[0]}/{total}] ✗ {nreg}: {e}")
                return False

        # Process documents in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_single, nreg): nreg for nreg in nregs}

            for future in as_completed(futures):
                try:
                    if future.result():
                        processed += 1
                except Exception as e:
                    nreg = futures[future]
                    logger.error(f"Unexpected error for {nreg}: {e}")

        return processed

    def process_recent_updates(self, pages: int = 1) -> int:
        """
        Process recently updated documents.
        Useful for incremental updates.
        """
        logger.info("=" * 60)
        logger.info("Processing Recent Updates")
        logger.info("=" * 60)

        processed = 0

        for page in range(1, pages + 1):
            docs = self.api_client.get_recent_documents(page)
            logger.info(f"Page {page}: {len(docs)} documents")

            for doc_info in docs:
                nreg = doc_info.get('nreg', '')
                if nreg:
                    try:
                        document = self.process_document(nreg)
                        if document:
                            processed += 1
                    except Exception as e:
                        logger.error(f"Error processing {nreg}: {e}")

        return processed

    def run_full_pipeline(
        self,
        include_constitution: bool = True,
        include_codes: bool = True,
        include_laws: bool = True,
        include_international: bool = False,
        limit: Optional[int] = None,
        max_workers: int = 4
    ) -> Dict[str, Any]:
        """
        Run the complete pipeline.

        Args:
            include_constitution: Process Constitution of Ukraine
            include_codes: Process major legal codes
            include_laws: Process primary legislative acts
            include_international: Include international treaties
            limit: Maximum number of laws to process
            max_workers: Number of parallel download threads
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("Starting Ukrainian Legal Documents RAG Pipeline")
        logger.info(f"Time: {start_time.isoformat()}")
        logger.info(f"Threads: {max_workers}")
        logger.info("=" * 60)

        results = {
            'constitution_processed': False,
            'codes_processed': 0,
            'laws_processed': 0
        }

        try:
            # Step 1: Process Constitution
            if include_constitution:
                results['constitution_processed'] = self.process_constitution() is not None

            # Step 2: Process major codes
            if include_codes:
                results['codes_processed'] = len(self.process_codes())

            # Step 3: Process all primary laws
            if include_laws:
                results['laws_processed'] = self.process_primary_acts(
                    include_international=include_international,
                    limit=limit,
                    max_workers=max_workers
                )

        except KeyboardInterrupt:
            logger.warning("Pipeline interrupted by user")
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)

        # Final stats
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("Pipeline Complete")
        logger.info(f"Duration: {duration:.1f} seconds")
        logger.info(f"Documents processed: {self.stats['documents_processed']}")
        logger.info(f"Documents skipped: {self.stats['skipped']}")
        logger.info(f"Documents uploaded: {self.stats['documents_uploaded']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info("=" * 60)

        return {
            **results,
            'stats': self.stats,
            'duration_seconds': duration
        }

    def generate_index(self) -> UploadResult:
        """
        Generate and upload a document index.
        """
        logger.info("Generating document index...")

        index_data = []

        # Get all processed documents info
        for nreg in self.processed_docs:
            safe_nreg = nreg.replace("/", "_").replace("\\", "_")
            index_data.append({
                'nreg': nreg,
                'safe_nreg': safe_nreg,
                'processed_at': datetime.now().isoformat()
            })

        return self.uploader.upload_index(index_data)
