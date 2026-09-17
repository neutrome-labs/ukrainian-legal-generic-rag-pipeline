"""Observable streaming import progress; all text and storage I/O is mocked."""

import json
import logging
import re
import sys
from unittest.mock import Mock

import pytest

from src.cli import edrsr
from src.sources.edrsr import importer
from src.storage import UploadResult

URL = "https://od.reyestr.court.gov.ua/files/test.rtf"
ROW = {"doc_id": "123", "doc_url": URL, "status": "1", "judgment_name": "Рішення", "cause_num": "1/26"}


@pytest.fixture
def setup_import(monkeypatch, tmp_path):
    rows = [ROW.copy()]
    export = Mock()
    export.iter_records.side_effect = lambda: (row.copy() for row in rows)
    monkeypatch.setattr(importer, "AnnualExport", Mock(return_value=export))
    loader = Mock()
    loader.load.return_value = "Текст рішення"
    monkeypatch.setattr(edrsr, "DecisionTextLoader", Mock(return_value=loader))
    storage = Mock()
    storage.get_processed_doc_ids.return_value = set()
    storage.upload_document.return_value = UploadResult(True, "document.md")
    storage.upload_metadata.return_value = UploadResult(True, "metadata.json")
    monkeypatch.setattr(edrsr, "get_uploader", Mock(return_value=storage))
    monkeypatch.setattr(edrsr, "load_config", Mock(return_value=Mock(cache_dir=str(tmp_path), r2=Mock())))
    return rows, loader, storage


def test_progress_defaults_and_json_stdout(setup_import, capsys):
    assert edrsr.build_parser().parse_args(["2025"]).progress is True
    edrsr.main(["2025", "--limit", "1"])
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"processed": 1, "skipped": 0, "errors": 0,
                                       "rows_read": 1, "non_public": 0, "existing": 0}
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO Starting", captured.err)
    for message in ("storage=local", "limit=1 input rows", "Scanning source directory", "Loading document id=123",
                    "Saved id=123", "processed=1", "elapsed=", "rate=", "Import complete"):
        assert message in captured.err
    assert "ETA" not in captured.err


def test_progress_is_flushed_before_loader_and_text_logs_visible(setup_import, capsys):
    _, loader, _ = setup_import
    def load(url):
        # The loader may block for a download delay; progress must already be visible.
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"Loading document id=123 url={URL}" in captured.err
        logging.getLogger("src.sources.edrsr.texts").info("Cache hit test event")
        return "Текст"
    loader.load.side_effect = load
    edrsr.main(["2025"])
    assert "Cache hit test event" in capsys.readouterr().err


def test_no_progress(setup_import, capsys):
    edrsr.main(["2025", "--no-progress"])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["processed"] == 1


def test_dry_run_does_not_configure_progress(setup_import, monkeypatch, capsys):
    _, loader, storage = setup_import
    monkeypatch.setattr(edrsr, "progress_logging", Mock(side_effect=AssertionError("no logging setup")))
    edrsr.main(["2025", "--dry-run", "--remote", "--skip-existing", "--progress"])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == ROW
    loader.load.assert_not_called()
    storage.get_processed_doc_ids.assert_not_called()
    edrsr.get_uploader.assert_not_called()


def test_remote_existing_ids_loaded_once_and_skipped_scan_progress(setup_import, capsys):
    rows, loader, storage = setup_import
    rows[:] = [{**ROW, "status": "0"}] * 500 + [ROW] * 500
    storage.get_processed_doc_ids.return_value = {"123"}
    def existing_ids():
        assert "Loading existing document IDs from remote (R2) storage" in capsys.readouterr().err
        return {"123"}
    storage.get_processed_doc_ids.side_effect = existing_ids
    edrsr.main(["2025", "--remote", "--skip-existing"])
    captured = capsys.readouterr()
    assert "Loaded 1 existing document IDs" in captured.err
    assert "Scan progress: rows_read=1000 non_public=500 existing=500" in captured.err
    assert json.loads(captured.out)["existing"] == 500
    storage.get_processed_doc_ids.assert_called_once()
    loader.load.assert_not_called()
    storage.upload_document.assert_not_called()


@pytest.mark.parametrize("failure,code", [(ValueError("bad text"), 1), (KeyboardInterrupt(), 130)])
@pytest.mark.parametrize("progress", [True, False])
def test_failure_context_partial_summary_and_cleanup(setup_import, capsys, failure, code, progress):
    rows, loader, _ = setup_import
    rows.append({**ROW, "doc_id": "456"})
    loader.load.side_effect = ["Текст", failure]
    parent = logging.getLogger("src")
    texts = logging.getLogger("src.sources.edrsr.texts")
    before = (parent.handlers[:], parent.level, parent.propagate, texts.level)
    with pytest.raises(SystemExit) as error:
        edrsr.main(["2025", "--progress" if progress else "--no-progress"])
    assert error.value.code == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Import failed id=456 url={URL}" in captured.err
    assert "--skip-existing" in captured.err
    assert ("Partial import: processed=1" in captured.err) is progress
    assert (parent.handlers, parent.level, parent.propagate, texts.level) == before
    loader.close.assert_called_once()


@pytest.mark.parametrize("method", ["upload_document", "upload_metadata"])
@pytest.mark.parametrize("progress", [True, False])
def test_save_failure_has_context_and_totals(setup_import, capsys, method, progress):
    _, _, storage = setup_import
    getattr(storage, method).return_value = UploadResult(False, "key", error="disk full")
    with pytest.raises(SystemExit) as error:
        edrsr.main(["2025", "--progress" if progress else "--no-progress"])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["errors"] == 1
    assert f"Save failed id=123 url={URL}" in captured.err
    assert "processed=0 skipped=0 errors=1" in captured.err
    assert "disk full" in captured.err


def test_listing_failure_has_partial_summary(setup_import, capsys):
    _, loader, storage = setup_import
    storage.get_processed_doc_ids.side_effect = OSError("listing failed")
    with pytest.raises(SystemExit) as error:
        edrsr.main(["2025", "--remote", "--skip-existing"])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Partial import: processed=0" in captured.err
    assert "listing failed" in captured.err
    loader.load.assert_not_called()
    loader.close.assert_called_once()


@pytest.mark.parametrize("failure,code", [(OSError("write failed"), 1), (KeyboardInterrupt(), 130)])
def test_raised_save_failure_keeps_current_context(setup_import, capsys, failure, code):
    _, _, storage = setup_import
    storage.upload_document.side_effect = failure
    with pytest.raises(SystemExit) as error:
        edrsr.main(["2025"])
    assert error.value.code == code
    captured = capsys.readouterr()
    assert f"Import failed id=123 url={URL}" in captured.err
    assert "Partial import: processed=0" in captured.err


def test_existing_set_tracks_new_saves_before_next_load(setup_import, capsys):
    rows, loader, storage = setup_import
    rows.append(ROW.copy())
    edrsr.main(["2025", "--skip-existing"])
    stats = json.loads(capsys.readouterr().out)
    assert stats["processed"] == 1 and stats["existing"] == 1
    storage.get_processed_doc_ids.assert_called_once()
    loader.load.assert_called_once()


def test_success_restores_logging_even_with_stdout_parent_handler(setup_import, capsys):
    parent = logging.getLogger("src")
    before = (parent.handlers[:], parent.level, parent.propagate)
    handler = logging.StreamHandler(sys.stdout)
    parent.addHandler(handler)
    try:
        edrsr.main(["2025"])
        assert parent.handlers == before[0] + [handler]
        assert (parent.level, parent.propagate) == before[1:]
        assert json.loads(capsys.readouterr().out)["processed"] == 1
    finally:
        parent.removeHandler(handler)
        handler.close()
