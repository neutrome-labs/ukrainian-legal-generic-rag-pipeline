"""
Incremental sync script for Ukrainian Legal Documents.
Fetches recently updated documents and syncs them to R2.

Usage:
  python sync_updates.py                    # Sync last 24 hours of updates
  python sync_updates.py --pages 5          # Sync 5 pages of recent updates
  python sync_updates.py --local            # Save locally instead of R2
  python sync_updates.py --schedule         # Run on schedule (every 6 hours)
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from src.config import load_config
from src.rada_api_client import RadaAPIClient
from src.markdown_converter import MarkdownConverter
from src.r2_uploader import get_uploader
from pipeline import LegalDocumentPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('sync.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


def sync_recent_updates(
    pages: int = 1,
    use_local: bool = False,
    output_dir: str = "./output"
) -> dict:
    """
    Sync recently updated documents to storage.

    Args:
        pages: Number of pages of recent updates to fetch
        use_local: Use local storage instead of R2
        output_dir: Output directory for local storage

    Returns:
        Statistics dictionary
    """
    logger.info(f"Starting incremental sync at {datetime.now().isoformat()}")
    logger.info(f"Fetching {pages} page(s) of recent updates")

    config = load_config()
    config.output_dir = output_dir

    pipeline = LegalDocumentPipeline(
        config=config,
        use_local_storage=use_local
    )

    processed = pipeline.process_recent_updates(pages=pages)

    stats = {
        'timestamp': datetime.now().isoformat(),
        'pages_fetched': pages,
        'documents_processed': processed,
        'documents_uploaded': pipeline.stats['documents_uploaded'],
        'errors': pipeline.stats['errors']
    }

    logger.info(f"Sync complete: {processed} documents processed")
    return stats


def run_scheduled(interval_hours: int = 6, pages: int = 1, use_local: bool = False):
    """
    Run sync on a schedule.

    Args:
        interval_hours: Hours between syncs
        pages: Number of pages to fetch each time
        use_local: Use local storage
    """
    logger.info(f"Starting scheduled sync (every {interval_hours} hours)")

    while True:
        try:
            stats = sync_recent_updates(pages=pages, use_local=use_local)
            logger.info(f"Scheduled sync complete: {stats}")
        except Exception as e:
            logger.error(f"Scheduled sync failed: {e}", exc_info=True)

        # Sleep until next sync
        sleep_seconds = interval_hours * 3600
        logger.info(f"Next sync in {interval_hours} hours...")
        time.sleep(sleep_seconds)


def main():
    parser = argparse.ArgumentParser(
        description="Sync recent Ukrainian legal document updates to R2"
    )

    parser.add_argument(
        '--pages',
        type=int,
        default=1,
        help='Number of pages of recent updates to fetch (default: 1)'
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
        '--schedule',
        action='store_true',
        help='Run on schedule (default: every 6 hours)'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=6,
        help='Hours between scheduled syncs (default: 6)'
    )

    args = parser.parse_args()

    if args.schedule:
        run_scheduled(
            interval_hours=args.interval,
            pages=args.pages,
            use_local=args.local
        )
    else:
        stats = sync_recent_updates(
            pages=args.pages,
            use_local=args.local,
            output_dir=args.output_dir
        )
        print(f"\nSync Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
