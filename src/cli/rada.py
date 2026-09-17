"""Command-line entry point for the Rada legal document pipeline."""

import argparse
import json
import logging
import sys

from src.config import load_config
from src.pipelines.rada import LegalDocumentPipeline

logger = logging.getLogger(__name__)


def main():
    """Main entry point with CLI arguments"""
    parser = argparse.ArgumentParser(
        description="Ukrainian Legal Documents RAG Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process only the Constitution (for testing)
  python -m src.cli.rada --constitution-only

  # Process Constitution and Codes
  python -m src.cli.rada --include-codes --limit 0

  # Full pipeline with limit
  python -m src.cli.rada --limit 100

  # Full pipeline including international treaties
  python -m src.cli.rada --include-international

  # Use local storage instead of R2
  python -m src.cli.rada --local --limit 10
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

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('pipeline.log', encoding='utf-8')
        ]
    )
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
