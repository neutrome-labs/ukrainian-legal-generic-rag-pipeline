"""
Upload existing local Markdown documents to R2.
Each Markdown file is a complete source document for Cloudflare AI Search.
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

from src.cli.common import add_boolean_argument, add_dry_run_argument
from src.config import load_config
from src.storage import R2Uploader, UploadResult

logger = logging.getLogger(__name__)


def upload_file(uploader: R2Uploader, local_path: Path, r2_key: str) -> UploadResult:
    """Upload a single file to R2"""
    try:
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()

        result = uploader.client.put_object(
            Bucket=uploader.config.bucket_name,
            Key=r2_key,
            Body=content.encode('utf-8'),
            ContentType='text/markdown; charset=utf-8'
        )

        return UploadResult(
            success=True,
            key=r2_key,
            etag=result.get('ETag', '').strip('"'),
            size=len(content.encode('utf-8'))
        )
    except Exception as e:
        return UploadResult(
            success=False,
            key=r2_key,
            error=str(e)
        )


def upload_directory(
    uploader: R2Uploader,
    local_dir: Path,
    max_workers: int = 10,
    dry_run: bool = False,
    skip_existing: bool = True
) -> Tuple[int, int, int]:
    """
    Upload all files from local directory to R2.
    Returns (uploaded, skipped, failed) counts.
    """
    # Collect all files to upload
    files_to_upload: List[Tuple[Path, str]] = []

    for root, dirs, files in os.walk(local_dir):
        for filename in files:
            local_path = Path(root) / filename
            # Get relative path from output dir as R2 key
            rel_path = local_path.relative_to(local_dir)
            r2_key = str(rel_path)
            files_to_upload.append((local_path, r2_key))

    total = len(files_to_upload)
    logger.info(f"Found {total} files to upload")

    if dry_run:
        logger.info("DRY RUN - no files will be uploaded")
        for local_path, r2_key in files_to_upload[:10]:
            logger.info(f"  Would upload: {r2_key}")
        if total > 10:
            logger.info(f"  ... and {total - 10} more files")
        return total, 0, 0

    uploaded = 0
    skipped = 0
    failed = 0

    # Only query existing keys when skipping is requested.
    existing_keys = set()
    if skip_existing:
        logger.info("Checking existing files in R2...")
        try:
            paginator = uploader.client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=uploader.config.bucket_name):
                for obj in page.get('Contents', []):
                    existing_keys.add(obj['Key'])
            logger.info(f"Found {len(existing_keys)} existing files in R2")
        except Exception as e:
            logger.warning(f"Could not list existing files: {e}")

    # Filter out already uploaded files
    files_to_upload_new = [(lp, k) for lp, k in files_to_upload if k not in existing_keys]
    skipped = len(files_to_upload) - len(files_to_upload_new)

    if skipped > 0:
        logger.info(f"Skipping {skipped} files that already exist in R2")

    if not files_to_upload_new:
        logger.info("All files already uploaded!")
        return 0, skipped, 0

    logger.info(f"Uploading {len(files_to_upload_new)} files...")

    # Upload in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(upload_file, uploader, local_path, r2_key): (local_path, r2_key)
            for local_path, r2_key in files_to_upload_new
        }

        for i, future in enumerate(as_completed(futures), 1):
            local_path, r2_key = futures[future]
            try:
                result = future.result()
                if result.success:
                    uploaded += 1
                    if uploaded % 100 == 0 or uploaded == len(files_to_upload_new):
                        logger.info(f"Progress: {uploaded}/{len(files_to_upload_new)} uploaded")
                else:
                    failed += 1
                    logger.error(f"Failed to upload {r2_key}: {result.error}")
            except Exception as e:
                failed += 1
                logger.error(f"Error uploading {r2_key}: {e}")

    return uploaded, skipped, failed


def build_parser():
    parser = argparse.ArgumentParser(
        description="Upload existing local files to Cloudflare R2"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default="./output",
        help="Local directory with files to upload (default: ./output)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Number of parallel upload workers (default: 10)"
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        default=True,
        help="Upload to remote storage (R2; the only destination)"
    )
    add_dry_run_argument(parser)
    add_boolean_argument(
        parser, "--skip-existing", default=True,
        help="Skip files that already exist in R2"
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error('--workers must be at least 1')

    if args.dry_run:
        print('DRY RUN - upload plan (no changes will be made)')
        print(f'  Input directory: {args.input_dir}')
        print('  Storage: remote (R2)')
        print(f'  Workers: {args.workers}')
        print(f'  Skip existing: {args.skip_existing}')
        input_dir = Path(args.input_dir)
        if not input_dir.is_dir():
            parser.exit(1, f"Input directory not found: {input_dir}\n")
        total = 0
        for root, _, files in os.walk(input_dir):
            for filename in files:
                if total < 10:
                    print(f'  Would upload: {(Path(root) / filename).relative_to(input_dir)}')
                total += 1
        print(f'  Files considered: {total} (showing at most 10)')
        print('  No configuration or remote storage will be accessed; existing keys are not checked.')
        return

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        logger.error(f"Input directory not found: {input_dir}")
        sys.exit(1)

    # Initialize R2 uploader
    try:
        config = load_config()
        uploader = R2Uploader(config.r2)
        logger.info(f"Connected to R2 bucket: {config.r2.bucket_name}")
    except Exception as e:
        logger.error(f"Failed to initialize R2 uploader: {e}")
        sys.exit(1)

    # Upload all files
    uploaded, skipped, failed = upload_directory(
        uploader,
        input_dir,
        max_workers=args.workers,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing
    )

    # Summary
    logger.info("=" * 60)
    logger.info("Upload Complete!")
    logger.info(f"  Uploaded: {uploaded}")
    logger.info(f"  Skipped (already exists): {skipped}")
    logger.info(f"  Failed: {failed}")
    logger.info("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
