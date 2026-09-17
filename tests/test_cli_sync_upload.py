"""Offline regression tests for sync and upload CLI conventions."""

from unittest.mock import Mock

import pytest

from src.cli import sync, upload
from src.storage import UploadResult


def test_sync_parser_defaults():
    args = sync.build_parser().parse_args([])
    assert vars(args) == {
        "pages": 1, "local": False, "dry_run": False,
        "skip_existing": False, "output_dir": "./output",
        "schedule": False, "interval": 6,
    }
    # Incremental sync has never generated an index.
    assert "--generate-index" not in sync.build_parser().format_help()


def test_upload_parser_defaults():
    args = upload.build_parser().parse_args([])
    assert args.input_dir == "./output"
    assert args.workers == 10
    assert args.skip_existing is True
    assert args.dry_run is False
    assert args.remote is True
    assert upload.build_parser().parse_args(["--remote"]).remote is True


@pytest.mark.parametrize("module, names", [
    (sync, ["schedule", "skip-existing", "dry-run"]),
    (upload, ["skip-existing", "dry-run"]),
])
def test_boolean_pairs(module, names):
    parser = module.build_parser()
    for name in names:
        dest = name.replace("-", "_")
        assert getattr(parser.parse_args([f"--{name}"]), dest) is True
        assert getattr(parser.parse_args([f"--no-{name}"]), dest) is False
        assert getattr(parser.parse_args([f"--{name}", f"--no-{name}"]), dest) is False
        assert getattr(parser.parse_args([f"--no-{name}", f"--{name}"]), dest) is True


def test_storage_options():
    parser = sync.build_parser()
    assert parser.parse_args(["--local"]).local is True
    assert parser.parse_args(["--remote"]).local is False
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["--local", "--remote"])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        upload.build_parser().parse_args(["--local"])
    assert error.value.code == 2


@pytest.mark.parametrize("module, flag", [
    (sync, "--pages"), (sync, "--interval"), (upload, "--workers"),
])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_invalid_numbers_fail_before_side_effects(module, flag, value, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    config = Mock(side_effect=AssertionError("must not load config"))
    monkeypatch.setattr(module, "load_config", config)
    with pytest.raises(SystemExit) as error:
        module.main([flag, value, "--dry-run"])
    assert error.value.code == 2
    assert f"{flag} must be at least 1" in capsys.readouterr().err
    config.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("destination", ["--local", "--remote"])
def test_sync_dry_run_has_no_side_effects(destination, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    forbidden = Mock(side_effect=AssertionError("dry run must not initialize anything"))
    for name in ["load_config", "LegalDocumentPipeline", "sync_recent_updates", "run_scheduled"]:
        monkeypatch.setattr(sync, name, forbidden)
    monkeypatch.setattr(sync.logging, "FileHandler", forbidden)
    monkeypatch.setattr(sync.logging, "basicConfig", forbidden)
    monkeypatch.setattr(sync.time, "sleep", forbidden)
    sync.main(["--dry-run", "--schedule", destination, "--pages", "3",
               "--interval", "2", "--skip-existing", "--output-dir", "planned-output"])
    output = capsys.readouterr().out
    assert "DRY RUN" in output
    assert "Pages: 3" in output
    assert "Schedule: True; interval: 2 hours" in output
    assert "Skip existing: True" in output
    assert "planned-output" in output
    assert ("Storage: local" if destination == "--local" else "Storage: remote (R2)") in output
    forbidden.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_upload_dry_run_has_no_side_effects(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    forbidden = Mock(side_effect=AssertionError("dry run must not access storage"))
    for name in ["load_config", "R2Uploader", "upload_directory"]:
        monkeypatch.setattr(upload, name, forbidden)
    monkeypatch.setattr(upload.logging, "basicConfig", forbidden)
    (tmp_path / "document.md").write_text("unchanged", encoding="utf-8")
    upload.main(["--dry-run", "--remote", "--input-dir", str(tmp_path),
                 "--workers", "1", "--no-skip-existing"])
    output = capsys.readouterr().out
    assert "DRY RUN" in output
    assert "Would upload: document.md" in output
    assert "Workers: 1" in output
    assert "Skip existing: False" in output
    forbidden.assert_not_called()
    assert list(tmp_path.iterdir()) == [tmp_path / "document.md"]
    assert (tmp_path / "document.md").read_text() == "unchanged"


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("skip_existing", [False, True])
def test_sync_main_forwards_options(scheduled, skip_existing, monkeypatch):
    once = Mock(return_value={"documents_processed": 1})
    schedule = Mock()
    monkeypatch.setattr(sync, "sync_recent_updates", once)
    monkeypatch.setattr(sync, "run_scheduled", schedule)
    monkeypatch.setattr(sync.logging, "basicConfig", Mock())
    monkeypatch.setattr(sync.logging, "FileHandler", Mock())
    sync.main(["--dry-run", "--no-dry-run", "--local", "--pages", "2",
               "--interval", "1", "--output-dir", "custom",
               "--schedule" if scheduled else "--no-schedule",
               "--skip-existing" if skip_existing else "--no-skip-existing"])
    expected = dict(pages=2, use_local=True, output_dir="custom", skip_existing=skip_existing)
    if scheduled:
        schedule.assert_called_once_with(interval_hours=1, **expected)
        once.assert_not_called()
    else:
        once.assert_called_once_with(**expected)
        schedule.assert_not_called()


@pytest.mark.parametrize("skip_existing", [False, True])
def test_sync_recent_updates_forwards_skip_existing(skip_existing, monkeypatch):
    config = Mock()
    pipeline = Mock(stats={"documents_uploaded": 2, "errors": 0})
    pipeline.process_recent_updates.return_value = 2
    factory = Mock(return_value=pipeline)
    monkeypatch.setattr(sync, "load_config", Mock(return_value=config))
    monkeypatch.setattr(sync, "LegalDocumentPipeline", factory)
    stats = sync.sync_recent_updates(3, True, "custom", skip_existing)
    factory.assert_called_once_with(config=config, use_local_storage=True, skip_existing=skip_existing)
    assert config.output_dir == "custom"
    pipeline.process_recent_updates.assert_called_once_with(pages=3)
    pipeline.generate_index.assert_not_called()
    assert stats["documents_processed"] == 2


def test_scheduled_sync_forwards_skip_existing(monkeypatch):
    once = Mock(return_value={})
    sleep = Mock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(sync, "sync_recent_updates", once)
    monkeypatch.setattr(sync.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        sync.run_scheduled(1, 2, True, "custom", True)
    once.assert_called_once_with(pages=2, use_local=True, output_dir="custom", skip_existing=True)
    sleep.assert_called_once_with(3600)


@pytest.mark.parametrize("skip_existing", [False, True])
def test_upload_main_forwards_options(skip_existing, monkeypatch, tmp_path):
    config = Mock()
    uploader = Mock()
    factory = Mock(return_value=uploader)
    directory = Mock(return_value=(1, 0, 0))
    monkeypatch.setattr(upload, "load_config", Mock(return_value=config))
    monkeypatch.setattr(upload, "R2Uploader", factory)
    monkeypatch.setattr(upload, "upload_directory", directory)
    monkeypatch.setattr(upload.logging, "basicConfig", Mock())
    upload.main(["--input-dir", str(tmp_path), "--workers", "1", "--remote",
                 "--dry-run", "--no-dry-run",
                 "--skip-existing" if skip_existing else "--no-skip-existing"])
    factory.assert_called_once_with(config.r2)
    directory.assert_called_once_with(uploader, tmp_path, max_workers=1,
                                      dry_run=False, skip_existing=skip_existing)


@pytest.mark.parametrize("skip_existing", [None, True, False])
def test_upload_directory_honors_skip_existing(skip_existing, monkeypatch, tmp_path):
    (tmp_path / "existing.md").write_text("old", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "new.md").write_text("new", encoding="utf-8")
    uploader = Mock()
    uploader.client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "existing.md"}]}, {},
    ]
    send = Mock(side_effect=lambda _, path, key: UploadResult(True, key))
    monkeypatch.setattr(upload, "upload_file", send)
    kwargs = {} if skip_existing is None else {"skip_existing": skip_existing}
    result = upload.upload_directory(uploader, tmp_path, max_workers=1, **kwargs)
    keys = {call.args[2] for call in send.call_args_list}
    if skip_existing is False:
        assert result == (2, 0, 0)
        assert keys == {"existing.md", "nested/new.md"}
        uploader.client.get_paginator.assert_not_called()
    else:
        assert result == (1, 1, 0)
        assert keys == {"nested/new.md"}
        uploader.client.get_paginator.assert_called_once_with("list_objects_v2")


def test_upload_directory_dry_run_never_uses_uploader(monkeypatch, tmp_path):
    (tmp_path / "document.md").write_text("document", encoding="utf-8")
    send = Mock(side_effect=AssertionError("must not upload"))
    monkeypatch.setattr(upload, "upload_file", send)
    # An object with no attributes makes any uploader access fail.
    assert upload.upload_directory(object(), tmp_path, dry_run=True) == (1, 0, 0)
    send.assert_not_called()
