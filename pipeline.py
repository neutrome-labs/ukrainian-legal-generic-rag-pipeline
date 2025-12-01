#!/usr/bin/env python3
"""
Ukrainian Legal Documents RAG Pipeline
Main orchestrator for fetching, processing, and uploading legal documents
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Set

from src.config import (
    CONSTITUTION_NREG,
    DOC_STATUS_ACTIVE,
    PipelineConfig,
    load_config,
)
from src.rada_api_client import RadaAPIClient, LawDocument
from src.markdown_converter import MarkdownConverter, ConstitutionProcessor, DocumentChunk
from src.r2_uploader import R2Uploader, LocalStorage, get_uploader, UploadResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('pipeline.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class LegalDocumentPipeline:
    """
    Main pipeline for processing Ukrainian legal documents.
    
    Workflow:
    1. Fetch document list from Rada Open Data
    2. Download document text and metadata
    3. Convert to markdown chunks
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
        self.converter = MarkdownConverter(self.config.chunking)
        self.constitution_processor = ConstitutionProcessor(self.converter)
        self.skip_existing = skip_existing
        
        # Initialize storage
        self.uploader = get_uploader(
            use_local=use_local_storage,
            output_dir=self.config.output_dir
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
            'chunks_created': 0,
            'chunks_uploaded': 0,
            'errors': 0,
            'skipped': 0
        }
        self._stats_lock = Lock()  # Lock for thread-safe stats updates
    
    def process_constitution(self) -> List[DocumentChunk]:
        """
        Process the Constitution of Ukraine with special handling.
        Creates individual markdown files for each article.
        """
        logger.info("=" * 60)
        logger.info("Processing Constitution of Ukraine (Конституція України)")
        logger.info("=" * 60)
        
        # Fetch the constitution
        constitution = self.api_client.get_constitution()
        if not constitution:
            logger.error("Failed to fetch Constitution")
            self.stats['errors'] += 1
            return []
        
        logger.info(f"Title: {constitution.nazva}")
        logger.info(f"Status: {constitution.status_name} (code: {constitution.status})")
        
        # Get the text
        text = self.api_client.get_document_text(CONSTITUTION_NREG)
        if not text:
            logger.error("Failed to fetch Constitution text")
            self.stats['errors'] += 1
            return []
        
        # Process into chunks
        chunks = self.constitution_processor.process(
            text=text,
            structure=constitution.structure
        )
        
        logger.info(f"Created {len(chunks)} chunks from Constitution")
        
        # Upload chunks
        results = self.uploader.upload_chunks(chunks, doc_type="constitution")
        
        # Upload metadata
        metadata = {
            'nreg': constitution.nreg,
            'title': constitution.nazva,
            'adopted': constitution.date_adopted,
            'current_edition': constitution.date_current_edition,
            'chunk_count': len(chunks),
            'processed_at': datetime.now().isoformat(),
            'source': 'data.rada.gov.ua'
        }
        self.uploader.upload_metadata(
            constitution.nreg,
            metadata,
            doc_type="constitution"
        )
        
        # Update stats
        self.stats['documents_processed'] += 1
        self.stats['chunks_created'] += len(chunks)
        self.stats['chunks_uploaded'] += sum(1 for r in results if r.success)
        safe_nreg = constitution.nreg.replace("/", "_").replace("\\", "_")
        self.processed_docs.add(safe_nreg)
        
        return chunks
    
    def process_document(
        self,
        nreg: str,
        doc_type: str = "laws"
    ) -> List[DocumentChunk]:
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
                return []
        
        # Fetch full document
        doc_data = self.api_client.get_document_full(nreg)
        if not doc_data:
            logger.warning(f"Could not fetch document: {nreg}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return []
        
        doc = self.api_client.parse_document(doc_data)
        if not doc:
            logger.warning(f"Could not parse document: {nreg}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return []
        
        # Skip inactive documents if configured
        if self.config.process_active_laws_only and not doc.is_active:
            logger.debug(f"Skipping inactive document: {nreg}")
            return []
        
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
                return []
        
        # Create metadata
        metadata = {
            'nreg': doc.nreg,
            'dokid': doc.dokid,
            'status': 'active' if doc.is_active else 'inactive',
            'date_adopted': doc.date_adopted,
            'date_current_edition': doc.date_current_edition,
            'types': doc.types,
        }
        
        # Convert to chunks
        chunks = self.converter.process_document(
            doc_id=doc.safe_nreg,
            title=doc.nazva,
            text=text,
            structure=doc.structure,
            metadata=metadata
        )
        
        if not chunks:
            logger.warning(f"No chunks created for: {nreg}")
            return []
        
        # Upload chunks
        results = self.uploader.upload_chunks(chunks, doc_type=doc_type)
        
        # Upload document metadata
        full_metadata = {
            **metadata,
            'title': doc.nazva,
            'chunk_count': len(chunks),
            'processed_at': datetime.now().isoformat(),
            'source': 'data.rada.gov.ua'
        }
        self.uploader.upload_metadata(doc.safe_nreg, full_metadata, doc_type)
        
        # Thread-safe stats update
        with self._stats_lock:
            self.stats['documents_processed'] += 1
            self.stats['chunks_created'] += len(chunks)
            self.stats['chunks_uploaded'] += sum(1 for r in results if r.success)
        
        with self._docs_lock:
            self.processed_docs.add(safe_nreg)
        
        return chunks
    
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
    
    def process_codes(self) -> Dict[str, List[DocumentChunk]]:
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
                chunks = self.process_document(nreg, doc_type="code")
                if chunks:
                    results[nreg] = chunks
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
        
        # Get list of primary acts
        nregs = self.api_client.get_primary_acts_list(include_international)
        inactive = set(self.api_client.get_inactive_acts_list())
        
        # Filter out inactive if configured
        if self.config.process_active_laws_only:
            nregs = [n for n in nregs if n.strip() not in inactive]
        
        if limit:
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
                chunks = self.process_document(nreg, doc_type="laws")
                with progress_lock:
                    completed[0] += 1
                    if chunks:
                        logger.info(f"[{completed[0]}/{total}] ✓ {nreg} ({len(chunks)} chunks)")
                        return True
                    else:
                        logger.debug(f"[{completed[0]}/{total}] - {nreg} (skipped/no chunks)")
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
                        chunks = self.process_document(nreg)
                        if chunks:
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
            'constitution': None,
            'codes': {},
            'laws_processed': 0
        }
        
        try:
            # Step 1: Process Constitution
            if include_constitution:
                results['constitution'] = len(self.process_constitution())
            
            # Step 2: Process major codes
            if include_codes:
                results['codes'] = {
                    k: len(v) for k, v in self.process_codes().items()
                }
            
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
        logger.info(f"Chunks created: {self.stats['chunks_created']}")
        logger.info(f"Chunks uploaded: {self.stats['chunks_uploaded']}")
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


def main():
    """Main entry point with CLI arguments"""
    parser = argparse.ArgumentParser(
        description="Ukrainian Legal Documents RAG Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process only the Constitution (for testing)
  python pipeline.py --constitution-only
  
  # Process Constitution and Codes
  python pipeline.py --include-codes --limit 0
  
  # Full pipeline with limit
  python pipeline.py --limit 100
  
  # Full pipeline including international treaties
  python pipeline.py --include-international
  
  # Use local storage instead of R2
  python pipeline.py --local --limit 10
        """
    )
    
    parser.add_argument(
        '--constitution-only',
        action='store_true',
        help='Process only the Constitution'
    )
    
    parser.add_argument(
        '--include-codes',
        action='store_true',
        default=True,
        help='Include major codes (default: True)'
    )
    
    parser.add_argument(
        '--include-international',
        action='store_true',
        help='Include international treaties'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Limit number of primary acts to process'
    )
    
    parser.add_argument(
        '--threads',
        type=int,
        default=4,
        metavar='N',
        help='Number of parallel download threads (default: 4)'
    )
    
    parser.add_argument(
        '--local',
        action='store_true',
        help='Use local storage instead of R2'
    )
    
    parser.add_argument(
        '--output-dir',
        type=str,
        default='./output',
        help='Output directory for local storage'
    )
    
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        help='Skip documents already uploaded to R2/storage'
    )
    
    parser.add_argument(
        '--recent-only',
        type=int,
        default=None,
        metavar='PAGES',
        help='Process only recent updates (number of pages)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run quick test with limited documents'
    )
    
    args = parser.parse_args()
    
    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Load configuration
    config = load_config()
    if args.output_dir:
        config.output_dir = args.output_dir
    
    # Create pipeline
    pipeline = LegalDocumentPipeline(
        config=config,
        use_local_storage=args.local,
        skip_existing=args.skip_existing
    )
    
    # Run appropriate mode
    if args.test:
        logger.info("Running quick test...")
        pipeline.process_constitution()
        logger.info("Test complete!")
        return
    
    if args.constitution_only:
        pipeline.process_constitution()
        return
    
    if args.recent_only:
        pipeline.process_recent_updates(pages=args.recent_only)
        return
    
    # Full pipeline
    results = pipeline.run_full_pipeline(
        include_constitution=True,
        include_codes=args.include_codes,
        include_laws=True,
        include_international=args.include_international,
        limit=args.limit,
        max_workers=args.threads
    )
    
    # Generate index
    pipeline.generate_index()
    
    # Output summary
    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
