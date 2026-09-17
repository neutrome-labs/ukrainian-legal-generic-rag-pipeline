"""Bounded downloads and defensive extraction of official annual ЄДРСР ZIPs."""

import hashlib
import json
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

import requests

from .export import DOCUMENT_COLUMNS, LOOKUPS, read_rows

_COLUMNS = {"documents.csv": DOCUMENT_COLUMNS, **{
    f"{name}.csv": columns for name, (_, columns) in LOOKUPS.items()
}}
_CHUNK_SIZE = 64 * 1024


def _member_basename(member) -> str:
    """Validate even ignored entries; never use archive paths as output paths."""
    name = member.orig_filename
    parts = name.rstrip("/").split("/")
    mode = member.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if (not name or name.startswith("/") or "\\" in name or ":" in name
            or "\x00" in name or any(part in ("", ".", "..") for part in parts)):
        raise ValueError(f"Unsafe archive member path: {name!r}")
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ValueError(f"Unsupported archive member type: {name!r}")
    if member.flag_bits & 1:
        raise ValueError(f"Encrypted archive member: {name!r}")
    if (kind == stat.S_IFDIR and not member.is_dir()
            or kind == stat.S_IFREG and member.is_dir()):
        raise ValueError(f"Inconsistent archive member type: {name!r}")
    return parts[-1]


def _extract(archive_path: Path, staging: Path, max_unpacked_bytes: int) -> None:
    with ZipFile(archive_path) as archive:
        selected = {}
        seen = set()
        total = 0
        for member in archive.infolist():
            basename = _member_basename(member)
            total += member.file_size
            if member.file_size < 0 or total > max_unpacked_bytes:
                raise ValueError("Archive exceeds max_unpacked_bytes")
            if member.is_dir():
                continue
            if basename in seen:
                raise ValueError(f"Duplicate archive basename: {basename}")
            seen.add(basename)
            if basename in _COLUMNS:
                selected[basename] = member
        missing = _COLUMNS.keys() - selected.keys()
        if missing:
            raise ValueError(f"Archive missing required CSVs: {', '.join(sorted(missing))}")

        unpacked = 0
        for basename, member in selected.items():
            with archive.open(member) as source, (staging / basename).open("xb") as target:
                while chunk := source.read(_CHUNK_SIZE):
                    unpacked += len(chunk)
                    if unpacked > max_unpacked_bytes:
                        raise ValueError("Archive exceeds max_unpacked_bytes")
                    target.write(chunk)
            # Advancing validates the header (and at most one row), even for empty tables.
            rows = read_rows(staging / basename, _COLUMNS[basename])
            try:
                next(rows, None)
            finally:
                rows.close()


def download_export(
    dataset: dict, output_dir: str | Path,
    max_download_bytes: int = 2 * 1024**3,
    max_unpacked_bytes: int = 10 * 1024**3,
) -> Path:
    """Return a new ``edrsr-data-{year}`` directory; never merge/replace an existing one.

    Only HTTPS data.gov.ua resources are accepted, without redirects. Both limits
    are positive byte counts. Failures discard the ZIP and unpublished staging.
    """
    year = dataset["year"]
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValueError("dataset year must be an integer between 1 and 9999")
    for name, limit in (("max_download_bytes", max_download_bytes),
                        ("max_unpacked_bytes", max_unpacked_bytes)):
        if type(limit) is not int or limit <= 0:
            raise ValueError(f"{name} must be a positive integer")
    url = dataset["url"]
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("Unsupported archive URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "data.gov.ua"
            or not parsed.path.startswith("/") or parsed.fragment or "\\" in url):
        raise ValueError(f"Unsupported archive URL: {url!r}")

    output_dir = Path(output_dir)
    destination = output_dir / f"edrsr-data-{year}"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Export destination already exists: {destination}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".edrsr-", dir=output_dir) as temporary:
        workspace = Path(temporary)
        staging = workspace / "export"
        staging.mkdir()
        archive_path = workspace / "download.zip"
        digest = hashlib.sha256()
        downloaded = 0
        with requests.get(url, stream=True, allow_redirects=False, timeout=(10, 60)) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Unexpected HTTP {response.status_code}; redirects are not allowed")
            length = response.headers.get("Content-Length")
            if length is not None and (int(length) < 0 or int(length) > max_download_bytes):
                raise ValueError("Archive exceeds max_download_bytes")
            with archive_path.open("xb") as stream:
                for chunk in response.iter_content(_CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > max_download_bytes:
                        raise ValueError("Archive exceeds max_download_bytes")
                    digest.update(chunk)
                    stream.write(chunk)
        _extract(archive_path, staging, max_unpacked_bytes)
        archive_path.unlink()
        metadata = {
            **dataset,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "archive_sha256": digest.hexdigest(),
        }
        (staging / "_source.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        # Exclusive reservation also protects a destination created during download.
        # Only this newly created directory may be cleaned up if publication fails.
        destination.mkdir()
        try:
            for path in staging.iterdir():
                path.rename(destination / path.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination
