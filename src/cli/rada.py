"""Command-line entry point for the Rada legal document pipeline."""

import argparse
import json
import logging
import sys

from src.cli.common import add_boolean_argument, add_dry_run_argument, add_storage_arguments
from src.config import load_config
from src.pipelines.rada import LegalDocumentPipeline

logger = logging.getLogger(__name__)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Ukrainian Legal Documents RAG Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Only the Constitution, locally
  python -m src.cli.rada --local --no-include-codes --no-include-laws

  # Constitution and Codes
  python -m src.cli.rada --no-include-laws --workers 8

  # Full pipeline including international treaties, to R2
  python -m src.cli.rada --remote --include-international --limit 100

  # Preview selected stages without downloads or writes
  python -m src.cli.rada --dry-run

  # Recent updates (same as src.cli.sync --pages 2)
  python -m src.cli.rada --recent --pages 2
"""
    )
    for name, default, help_text in (
        ("constitution", True, "Include the Constitution"),
        ("codes", True, "Include major codes"),
        ("laws", True, "Include domestic primary acts"),
        ("international", False, "Include international treaties independently of domestic acts"),
    ):
        add_boolean_argument(parser, f"--include-{name}", default=default, help=help_text)
    parser.add_argument("--limit", type=int, help="Maximum primary acts (domestic and international); 0 disables that stage")
    parser.add_argument("--workers", type=int, default=4, help="Parallel download workers (default: 4)")
    add_storage_arguments(parser)
    parser.add_argument("--output-dir", default=None, help="Local output directory (default: OUTPUT_DIR or ./output)")
    add_boolean_argument(parser, "--skip-existing", help="Skip documents already saved to storage")
    add_boolean_argument(parser, "--active-only", default=True, help="Filter out inactive acts")
    add_boolean_argument(parser, "--generate-index", default=True, help="Generate the document index after a full run")
    add_boolean_argument(parser, "--recent", help="Process recent updates instead of the selected full-pipeline stages")
    parser.add_argument("--pages", type=int, default=1, help="Recent update pages (default: 1; used with --recent)")
    add_boolean_argument(parser, "--debug", help="Enable debug logging")
    add_dry_run_argument(parser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.pages < 1:
        parser.error("--pages must be positive")

    # A plan only: no pipeline, cache, log file or storage initialization.
    if args.dry_run:
        print(json.dumps({"dry_run": True, "mode": "recent" if args.recent else "full",
                          "storage": "local" if args.local else "remote", "options": vars(args)}, indent=2))
        return

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler('pipeline.log', encoding='utf-8')]
    )
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    config = load_config()
    if args.output_dir is not None:
        config.output_dir = args.output_dir
    config.process_active_laws_only = args.active_only
    pipeline = LegalDocumentPipeline(config=config, use_local_storage=args.local,
                                     skip_existing=args.skip_existing)
    if args.recent:
        pipeline.process_recent_updates(pages=args.pages)
        return

    results = pipeline.run_full_pipeline(
        include_constitution=args.include_constitution,
        include_codes=args.include_codes,
        include_laws=args.include_laws,
        include_international=args.include_international,
        limit=args.limit,
        max_workers=args.workers
    )
    if args.generate_index:
        pipeline.generate_index()

    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
