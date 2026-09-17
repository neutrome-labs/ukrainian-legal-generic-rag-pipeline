"""Discover official annual ЄДРСР exports; download into one cache root."""

import argparse
import json
from pathlib import Path

from src.cli.common import add_boolean_argument, add_dry_run_argument
from src.sources.edrsr.archive import download_export
from src.sources.edrsr.catalog import EdrsrCatalog

DEFAULT_EXPORTS = "exports"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=None,
                        help="Root cache directory (default: ./cache/edrsr); exports go to {cache-dir}/exports/")
    parser.add_argument("--years", nargs="+", type=int, help="Restrict to specific years")
    add_boolean_argument(parser, "--download", help="Download/extract selected ZIPs; otherwise list only")
    add_boolean_argument(parser, "--all-years", help="Explicitly select all discovered years for downloading")
    add_dry_run_argument(parser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.years and args.all_years:
        parser.error("choose --years or --all-years, not both")
    if args.download and not (args.years or args.all_years):
        parser.error("--download requires --years YEAR [...] or --all-years")
    cache_root = Path(args.cache_dir) if args.cache_dir else Path("cache") / "edrsr"
    exports_root = cache_root / DEFAULT_EXPORTS
    catalog = EdrsrCatalog()
    try:
        datasets = catalog.discover()
        if args.years:
            missing = set(args.years) - {item["year"] for item in datasets}
            if missing:
                raise ValueError(f"Years not found: {sorted(missing)}")
            datasets = [item for item in datasets if item["year"] in args.years]
        for item in datasets:
            print(json.dumps(item, ensure_ascii=False), flush=True)
        if not args.download or args.dry_run:
            return
        for item in datasets:
            destination = exports_root / f"edrsr-data-{item['year']}"
            if destination.exists():
                print(f"Preserving existing directory: {destination}", flush=True)
                continue
            print(f"Downloading {item['year']}...", flush=True)
            print(f"Ready: {download_export(item, exports_root)}", flush=True)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Dataset operation failed: {error}\n")
    finally:
        catalog.close()


if __name__ == "__main__":
    main()
