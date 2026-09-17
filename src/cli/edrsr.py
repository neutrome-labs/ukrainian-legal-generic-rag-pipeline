"""Import official ЄДРСР annual TSV directories and linked RTF documents."""

import argparse
import json
from pathlib import Path

import requests

from src.config import load_config
from src.pipelines.import_documents import ImportPipeline
from src.sources.edrsr.importer import EdrsrExportSource
from src.sources.edrsr.texts import DecisionTextLoader
from src.storage import get_uploader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", help="Extracted annual directories (default root: ./cache/edrsr/exports/); pass full paths or bare years")
    parser.add_argument("--preview", action="store_true", help="Print metadata only: no downloads, cache writes or R2 access")
    parser.add_argument("--output-dir", default=None, help="Markdown output root (default: CACHE_DIR/edrsr/documents)")
    parser.add_argument("--cache-dir", default=None, help="Root cache directory (default: CACHE_DIR/edrsr); RTFs under cache/, exports under cache/exports/")
    parser.add_argument("--offline", action="store_true", help="Use cached RTFs only; fail if a required file is missing")
    parser.add_argument("--delay", type=float, default=6.0, help="Seconds between downloads (default: 6)")
    parser.add_argument("--r2", action="store_true", help="Upload to R2 instead of saving under the cache root")
    parser.add_argument("--limit", type=int, help="Maximum input rows across all directories, before filtering; default preview: 10, import: unlimited")
    parser.add_argument("--skip-existing", action="store_true", help="Resume: skip existing IDs before downloading; does not detect revisions")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.delay < 0:
        parser.error("--delay must be non-negative")
    config = load_config()
    edrsr_root = Path(args.cache_dir) if args.cache_dir else Path(config.cache_dir) / "edrsr"
    loader = DecisionTextLoader(edrsr_root, args.delay, args.offline)
    resolved = [Path(directory) if "/" in directory or "\\" in directory or Path(directory).exists() else edrsr_root / "exports" / f"edrsr-data-{directory}" for directory in args.directories]
    source = EdrsrExportSource(resolved, loader, limit=args.limit if args.limit is not None else (10 if args.preview else None))
    try:
        if args.preview:
            for record in source.iter_records():
                print(json.dumps(record, ensure_ascii=False))
            return
        storage = get_uploader(use_local=not args.r2, output_dir=args.output_dir or edrsr_root / "documents",
                               config=config.r2, namespace="edrsr")
        if args.skip_existing:
            source.skip_ids = storage.get_processed_doc_ids()
        stats = ImportPipeline(source, storage).run(skip_existing=args.skip_existing)
        print(json.dumps({**stats, **source.stats}))
        if stats["errors"]:
            raise SystemExit(1)
    except (OSError, ValueError, requests.RequestException) as error:
        parser.exit(1, f"Import failed: {error}\nPrevious completed documents remain saved; rerun with --skip-existing to resume.\n")
    finally:
        loader.close()


if __name__ == "__main__":
    main()
