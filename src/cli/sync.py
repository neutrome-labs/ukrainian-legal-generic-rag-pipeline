"""
Incremental sync script for Ukrainian Legal Documents.
Fetches recently updated documents and syncs them to R2.

Usage:
  python -m src.cli.sync                    # Sync last 24 hours of updates
  python -m src.cli.sync --pages 5          # Sync 5 pages of recent updates
  python -m src.cli.sync --local            # Save locally instead of R2
  python -m src.cli.sync --schedule         # Run on schedule (every 6 hours)
"""

import argparse
import logging
import time
from datetime import datetime

from src.cli.common import add_boolean_argument, add_dry_run_argument, add_storage_arguments
from src.config import load_config
from src.pipelines.rada import LegalDocumentPipeline

logger = logging.getLogger(__name__)


def sync_recent_updates(
    pages: int = 1,
    use_local: bool = False,
    output_dir: str = "./output",
    skip_existing: bool = False
) -> dict:
    """
    Sync recently updated documents to storage.

    Args:
        pages: Number of pages of recent updates to fetch
        use_local: Use local storage instead of R2
        output_dir: Output directory for local storage
        skip_existing: Skip documents already in storage (may miss revisions)

    Returns:
        Statistics dictionary
    """
    logger.info(f"Starting incremental sync at {datetime.now().isoformat()}")
    logger.info(f"Fetching {pages} page(s) of recent updates")

    config = load_config()
    config.output_dir = output_dir

    pipeline = LegalDocumentPipeline(
        config=config,
        use_local_storage=use_local,
        skip_existing=skip_existing
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


def run_scheduled(
    interval_hours: int = 6,
    pages: int = 1,
    use_local: bool = False,
    output_dir: str = "./output",
    skip_existing: bool = False
):
    """
    Run sync on a schedule.

    Args:
        interval_hours: Hours between syncs
        pages: Number of pages to fetch each time
        use_local: Use local storage
        output_dir: Output directory for local storage
        skip_existing: Skip documents already in storage (may miss revisions)
    """
    logger.info(f"Starting scheduled sync (every {interval_hours} hours)")

    while True:
        try:
            stats = sync_recent_updates(
                pages=pages, use_local=use_local, output_dir=output_dir,
                skip_existing=skip_existing
            )
            logger.info(f"Scheduled sync complete: {stats}")
        except Exception as e:
            logger.error(f"Scheduled sync failed: {e}", exc_info=True)

        # Sleep until next sync
        sleep_seconds = interval_hours * 3600
        logger.info(f"Next sync in {interval_hours} hours...")
        time.sleep(sleep_seconds)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Sync recent Ukrainian legal document updates to R2"
    )

    parser.add_argument(
        '--pages',
        type=int,
        default=1,
        help='Number of pages of recent updates to fetch (default: 1)'
    )

    add_storage_arguments(parser)
    add_dry_run_argument(parser)
    add_boolean_argument(
        parser, '--skip-existing',
        help='Skip documents already in storage (may miss revisions)'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='./output',
        help='Output directory for local storage'
    )

    add_boolean_argument(
        parser, '--schedule',
        help='Run on schedule (default interval: every 6 hours)'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=6,
        help='Hours between scheduled syncs (default: 6)'
    )

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.pages < 1:
        parser.error('--pages must be at least 1')
    if args.interval < 1:
        parser.error('--interval must be at least 1')

    if args.dry_run:
        print('DRY RUN - sync plan (no changes will be made)')
        print(f"  Storage: {'local' if args.local else 'remote (R2)'}")
        print(f'  Output directory: {args.output_dir}')
        print(f'  Pages: {args.pages}')
        print(f'  Skip existing: {args.skip_existing}')
        print(f'  Schedule: {args.schedule}; interval: {args.interval} hours')
        print('  No configuration, pipeline, or scheduler will be initialized.')
        return

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('sync.log', encoding='utf-8')
        ]
    )

    if args.schedule:
        run_scheduled(
            interval_hours=args.interval,
            pages=args.pages,
            use_local=args.local,
            output_dir=args.output_dir,
            skip_existing=args.skip_existing
        )
    else:
        stats = sync_recent_updates(
            pages=args.pages,
            use_local=args.local,
            output_dir=args.output_dir,
            skip_existing=args.skip_existing
        )
        print(f"\nSync Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
