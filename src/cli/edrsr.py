"""Import official ЄДРСР annual TSV directories and linked RTF documents."""

import argparse
import json
import logging
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from src.cli.common import add_boolean_argument, add_dry_run_argument, add_storage_arguments
from src.config import load_config
from src.pipelines.import_documents import ImportPipeline
from src.sources.edrsr.importer import EdrsrExportSource
from src.sources.edrsr.texts import DecisionTextLoader
from src.storage import get_uploader

logger = logging.getLogger("src.cli.edrsr")


@contextmanager
def progress_logging(enabled):
    """Keep CLI logs on stderr without changing the caller's logging setup."""
    parent = logging.getLogger("src")
    names = ("src", "src.cli.edrsr", "src.sources.edrsr", "src.sources.edrsr.importer",
             "src.sources.edrsr.texts", "src.pipelines.import_documents")
    levels = {name: logging.getLogger(name).level for name in names}
    handlers, propagate = parent.handlers[:], parent.propagate
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO if enabled else logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    parent.handlers = [handler]
    parent.propagate = False
    for name in names:
        logging.getLogger(name).setLevel(logging.INFO)
    try:
        yield
    finally:
        parent.handlers = handlers
        parent.propagate = propagate
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        handler.close()


def log_summary(label, stats, source, started):
    elapsed = time.monotonic() - started
    logger.info("%s: processed=%d skipped=%d errors=%d rows_read=%d non_public=%d existing=%d elapsed=%.1fs rate=%.2f docs/s",
                label, stats.get("processed", 0), stats.get("skipped", 0), stats.get("errors", 0),
                source.stats.get("rows_read", 0), source.stats.get("non_public", 0), source.stats.get("existing", 0),
                elapsed, stats.get("processed", 0) / elapsed if elapsed > 0 else 0)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", help="Extracted annual directories (default root: ./cache/edrsr/exports/); pass full paths or bare years")
    add_dry_run_argument(parser)
    add_boolean_argument(parser, "--progress", default=True, help="Stream timestamped import progress to stderr (ignored for dry-run)")
    parser.add_argument("--output-dir", default=None, help="Markdown output root (default: CACHE_DIR/edrsr/documents)")
    parser.add_argument("--cache-dir", default=None, help="Root cache directory (default: CACHE_DIR/edrsr); RTFs under cache/, exports under cache/exports/")
    add_boolean_argument(parser, "--offline", help="Use cached RTFs only; fail if a required file is missing")
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds between downloads (default: 3)")
    add_storage_arguments(parser, default_local=True)
    parser.add_argument("--limit", type=int, help="Maximum input rows across all directories, before filtering; default dry-run: 10, import: unlimited")
    add_boolean_argument(parser, "--skip-existing", help="Resume: skip existing IDs before downloading; does not detect revisions")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.delay < 0:
        parser.error("--delay must be non-negative")
    config = load_config()
    edrsr_root = Path(args.cache_dir) if args.cache_dir else Path(config.cache_dir) / "edrsr"
    loader = DecisionTextLoader(edrsr_root, args.delay, args.offline)
    resolved = [Path(directory) if "/" in directory or "\\" in directory or Path(directory).exists() else edrsr_root / "exports" / f"edrsr-data-{directory}" for directory in args.directories]
    source = EdrsrExportSource(resolved, loader, limit=args.limit if args.limit is not None else (10 if args.dry_run else None))
    started = time.monotonic()
    pipeline = None
    # Preview does not install handlers, touch storage, or retrieve texts.
    with nullcontext() if args.dry_run else progress_logging(args.progress):
        try:
            if args.dry_run:
                for record in source.iter_records():
                    print(json.dumps(record, ensure_ascii=False))
                return
            destination = "local" if args.local else "remote (R2)"
            logger.info("Starting ЄДРСР import: inputs=%s storage=%s output=%s cache=%s limit=%s input rows offline=%s delay=%.1fs skip_existing=%s",
                        ", ".join(map(str, resolved)), destination,
                        (args.output_dir or edrsr_root / "documents") if args.local else "edrsr/",
                        edrsr_root, args.limit if args.limit is not None else "unlimited",
                        args.offline, args.delay, args.skip_existing)
            storage = get_uploader(use_local=args.local, output_dir=args.output_dir or edrsr_root / "documents",
                                   config=config.r2, namespace="edrsr")
            run_options = {}
            if args.skip_existing:
                logger.info("Loading existing document IDs from %s storage", destination)
                listing_started = time.monotonic()
                source.skip_ids = storage.get_processed_doc_ids()
                logger.info("Loaded %d existing document IDs from %s storage in %.1fs",
                            len(source.skip_ids), destination, time.monotonic() - listing_started)
                run_options["existing_ids"] = source.skip_ids
            pipeline = ImportPipeline(source, storage)
            stats = pipeline.run(skip_existing=args.skip_existing, **run_options)
            log_summary("Import complete", stats, source, started)
            print(json.dumps({**stats, **source.stats}))
            if stats["errors"]:
                parser.exit(1, f"Import completed with {stats['errors']} save errors. Previous completed documents remain saved; rerun with --skip-existing to resume.\n")
        except (Exception, KeyboardInterrupt) as error:
            if not args.dry_run:
                log_summary("Partial import", pipeline.stats if pipeline is not None else {}, source, started)
            record = source.current_record
            context = f" id={record['doc_id']} url={record['doc_url']}" if record else ""
            reason = "interrupted by user" if isinstance(error, KeyboardInterrupt) else str(error)
            parser.exit(130 if isinstance(error, KeyboardInterrupt) else 1,
                        f"Import failed{context}: {reason}\nPrevious completed documents remain saved; rerun with --skip-existing to resume.\n")
        finally:
            loader.close()


if __name__ == "__main__":
    main()
