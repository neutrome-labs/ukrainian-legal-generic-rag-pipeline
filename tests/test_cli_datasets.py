"""Offline checks for dataset CLI switches."""

from unittest.mock import Mock

import pytest

from src.cli import edrsr_datasets


def test_dataset_boolean_defaults_and_overrides():
    parser = edrsr_datasets.build_parser()
    args = parser.parse_args([])
    assert not args.download and not args.all_years and not args.dry_run
    args = parser.parse_args(["--download", "--no-download", "--all-years", "--no-all-years",
                              "--dry-run", "--no-dry-run"])
    assert not args.download and not args.all_years and not args.dry_run


@pytest.mark.parametrize("flags", [["--download", "--dry-run"], ["--no-download"]])
def test_dataset_listing_never_downloads(flags, monkeypatch, tmp_path):
    catalog = Mock()
    catalog.discover.return_value = [{"year": 2025}]
    monkeypatch.setattr(edrsr_datasets, "EdrsrCatalog", Mock(return_value=catalog))
    download = Mock(side_effect=AssertionError("must not download"))
    monkeypatch.setattr(edrsr_datasets, "download_export", download)
    root = tmp_path / "missing-cache"
    edrsr_datasets.main(["--years", "2025", "--cache-dir", str(root), *flags])
    download.assert_not_called()
    catalog.close.assert_called_once()
    assert not root.exists()


def test_dataset_download_requires_selection():
    with pytest.raises(SystemExit) as error:
        edrsr_datasets.main(["--download", "--dry-run"])
    assert error.value.code == 2
