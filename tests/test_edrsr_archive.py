"""Small in-memory archives and mocked HTTP; no live downloads."""

import hashlib
import io
import json
import stat
from datetime import datetime
from unittest.mock import MagicMock
from zipfile import BadZipFile, ZipFile, ZipInfo

import pytest
import requests

from src.sources.edrsr.archive import _member_basename, download_export
from src.sources.edrsr.export import DOCUMENT_COLUMNS, LOOKUPS

DATASET = {
    "year": 2025, "dataset_id": "dataset", "resource_id": "resource",
    "url": "https://data.gov.ua/dataset/test/resource/test/download/export.zip",
    "title": "Судові рішення", "extra": "preserved",
}
COLUMNS = {"documents": DOCUMENT_COLUMNS, **{
    name: columns for name, (_, columns) in LOOKUPS.items()
}}


def make_zip(prefix="", missing=None, bad_header=None, extra=None):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, columns in COLUMNS.items():
            if name != missing:
                header = "wrong" if name == bad_header else "\t".join(sorted(columns))
                archive.writestr(f"{prefix}{name}.csv", "\ufeff" + header + "\n")
        for name, contents in (extra or {}).items():
            archive.writestr(name, contents)
    return stream.getvalue()


@pytest.fixture
def http(monkeypatch):
    response = MagicMock(status_code=200, headers={})
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    get = MagicMock(return_value=response)
    monkeypatch.setattr("src.sources.edrsr.archive.requests.get", get)

    def serve(data, headers=None, status=200):
        response.status_code = status
        response.headers = headers or {}
        response.iter_content.return_value = [data[i:i + 31] for i in range(0, len(data), 31)]
        return get, response

    return serve


@pytest.mark.parametrize("prefix", ["", "edrsr-data-2025/", "exports/annual/"])
def test_success(tmp_path, http, prefix):
    payload = make_zip(prefix, extra={"notes.txt": "ignored"})
    get, response = http(payload)
    result = download_export(DATASET, tmp_path, max_download_bytes=len(payload))
    assert result == tmp_path / "edrsr-data-2025"
    assert set(path.name for path in result.iterdir()) == {
        *(f"{name}.csv" for name in COLUMNS), "_source.json",
    }
    metadata = json.loads((result / "_source.json").read_text())
    assert all(metadata[key] == value for key, value in DATASET.items())
    assert metadata["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    assert datetime.fromisoformat(metadata["fetched_at"]).utcoffset().total_seconds() == 0
    assert list(tmp_path.iterdir()) == [result]
    get.assert_called_once_with(DATASET["url"], stream=True, allow_redirects=False, timeout=(10, 60))
    response.__exit__.assert_called_once()


@pytest.mark.parametrize("url", [
    "http://data.gov.ua/export.zip", "https://example.com/export.zip",
    "https://data.gov.ua.example.com/export.zip", "https://user@data.gov.ua/export.zip",
])
def test_reject_url_before_network(tmp_path, http, url):
    get, _ = http(b"")
    with pytest.raises(ValueError, match="Unsupported archive URL"):
        download_export({**DATASET, "url": url}, tmp_path)
    get.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_no_redirects(tmp_path, http, status):
    _, response = http(b"", status=status)
    with pytest.raises(ValueError, match="redirects"):
        download_export(DATASET, tmp_path)
    response.iter_content.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["stream", "declared", "unpacked"])
def test_size_guards(tmp_path, http, kind):
    payload = make_zip()
    _, response = http(payload, {"Content-Length": str(len(payload))} if kind == "declared" else {})
    limits = {"max_unpacked_bytes": 1} if kind == "unpacked" else {"max_download_bytes": 1}
    with pytest.raises(ValueError, match="exceeds max_"):
        download_export(DATASET, tmp_path, **limits)
    if kind == "declared":
        response.iter_content.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("limit", ["max_download_bytes", "max_unpacked_bytes"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_limits(tmp_path, http, limit, value):
    get, _ = http(b"")
    with pytest.raises(ValueError, match="positive integer"):
        download_export(DATASET, tmp_path, **{limit: value})
    get.assert_not_called()


@pytest.mark.parametrize("name", list(COLUMNS))
def test_missing_required_csv(tmp_path, http, name):
    http(make_zip(missing=name))
    with pytest.raises(ValueError, match="missing required CSVs"):
        download_export(DATASET, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", list(COLUMNS))
def test_headers_validated(tmp_path, http, name):
    http(make_zip(bad_header=name))
    with pytest.raises(ValueError, match="missing columns"):
        download_export(DATASET, tmp_path)
    assert not list(tmp_path.iterdir())


def test_duplicate_basename(tmp_path, http):
    http(make_zip(extra={"nested/documents.csv": "doc_id\n"}))
    with pytest.raises(ValueError, match="Duplicate archive basename"):
        download_export(DATASET, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../documents.csv", "/documents.csv", "a\\documents.csv"])
def test_member_path_validation(name):
    with pytest.raises(ValueError, match="Unsafe archive member path"):
        _member_basename(ZipInfo(name))


@pytest.mark.parametrize("kind", ["symlink", "encrypted"])
def test_member_type_validation(kind):
    member = ZipInfo("documents.csv")
    if kind == "symlink":
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
    else:
        member.flag_bits = 1
    with pytest.raises(ValueError, match="Unsupported archive member type|Encrypted archive member"):
        _member_basename(member)


@pytest.mark.parametrize("failure", ["http", "stream", "zip"])
def test_failure_cleanup(tmp_path, http, failure):
    _, response = http(b"not a zip")
    expected = BadZipFile
    if failure == "http":
        response.raise_for_status.side_effect = requests.HTTPError("HTTP failure")
        expected = requests.HTTPError
    elif failure == "stream":
        response.iter_content.side_effect = requests.ConnectionError("interrupted")
        expected = requests.ConnectionError
    with pytest.raises(expected):
        download_export(DATASET, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("empty", [True, False])
def test_existing_directory_preserved(tmp_path, http, empty):
    destination = tmp_path / "edrsr-data-2025"
    destination.mkdir()
    if not empty:
        (destination / "user.txt").write_text("keep me")
    get, _ = http(make_zip())
    with pytest.raises(FileExistsError):
        download_export(DATASET, tmp_path)
    get.assert_not_called()
    assert destination.is_dir()
    assert list(tmp_path.iterdir()) == [destination]
    if not empty:
        assert (destination / "user.txt").read_text() == "keep me"


def test_destination_created_during_download_preserved(tmp_path, http):
    destination = tmp_path / "edrsr-data-2025"
    _, response = http(make_zip())

    def chunks(*args):
        destination.mkdir()
        yield make_zip()

    response.iter_content.side_effect = chunks
    with pytest.raises(FileExistsError):
        download_export(DATASET, tmp_path)
    assert destination.is_dir()
    assert not list(destination.iterdir())
    assert list(tmp_path.iterdir()) == [destination]
