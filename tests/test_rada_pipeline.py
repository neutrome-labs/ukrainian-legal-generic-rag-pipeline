"""Offline end-to-end Rada regression tests, with real local storage."""

import json
from unittest.mock import Mock

import pytest

from src.config import PipelineConfig
from src.pipelines.rada import LegalDocumentPipeline
from src.storage import UploadResult


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    config = PipelineConfig(cache_dir=str(tmp_path / "cache"), output_dir=str(tmp_path / "output"))
    instance = LegalDocumentPipeline(config, use_local_storage=True)
    monkeypatch.setattr(instance.api_client, "get_document_full", Mock(return_value={
        "doc": {"nreg": "123/45", "nazva": 'Закон "Тест"', "status": 5, "types": [1]},
    }))
    monkeypatch.setattr(instance.api_client, "get_document_text", Mock(return_value="Стаття 1. Норма\n\nПовний текст."))
    return instance


def test_rada_fetch_to_local_and_resume(pipeline):
    document = pipeline.process_document("123/45")
    assert document is not None
    root = pipeline.uploader.output_dir
    assert (root / "laws/123_45.md").read_text() == document.to_markdown()
    metadata = json.loads((root / "_metadata/laws/123_45.json").read_text())
    assert metadata["nreg"] == "123/45"
    assert metadata["source"] == "data.rada.gov.ua"
    resumed = LegalDocumentPipeline(pipeline.config, use_local_storage=True, skip_existing=True)
    assert resumed.process_document("123/45") is None
    assert resumed.stats["skipped"] == 1


@pytest.mark.parametrize("method", ["upload_document", "upload_metadata"])
def test_failed_write_is_retryable(pipeline, monkeypatch, method):
    original = getattr(pipeline.uploader, method)
    monkeypatch.setattr(pipeline.uploader, method, Mock(return_value=UploadResult(False, "key", error="test failure")))
    assert pipeline.process_document("123/45") is None
    assert "123_45" not in pipeline.processed_docs
    assert pipeline.stats["errors"] == 1
    monkeypatch.setattr(pipeline.uploader, method, original)
    assert pipeline.process_document("123/45") is not None


def test_zero_limit_does_not_fetch(pipeline, monkeypatch):
    fetch = Mock(side_effect=AssertionError("must not fetch"))
    monkeypatch.setattr(pipeline.api_client, "get_primary_acts_list", fetch)
    assert pipeline.process_primary_acts(limit=0) == 0
    fetch.assert_not_called()


def test_independent_international_stage(pipeline, monkeypatch):
    process = Mock(return_value=1)
    monkeypatch.setattr(pipeline, "process_primary_acts", process)
    constitution = Mock()
    codes = Mock()
    monkeypatch.setattr(pipeline, "process_constitution", constitution)
    monkeypatch.setattr(pipeline, "process_codes", codes)
    pipeline.run_full_pipeline(include_constitution=False, include_codes=False,
                               include_laws=False, include_international=True)
    constitution.assert_not_called()
    codes.assert_not_called()
    process.assert_called_once_with(include_international=True, limit=None, max_workers=4,
                                    include_domestic=False, exclude_codes=True)


def test_disabled_codes_not_reintroduced_by_primary_list(pipeline, monkeypatch):
    from src.config import CONSTITUTION_NREG
    code = pipeline.config.doc_types.priority_types["codes"][0]
    monkeypatch.setattr(pipeline.api_client, "get_primary_acts_list",
                        Mock(return_value=[CONSTITUTION_NREG, code, "123/45"]))
    inactive = Mock(side_effect=AssertionError("inactive filtering disabled"))
    monkeypatch.setattr(pipeline.api_client, "get_inactive_acts_list", inactive)
    pipeline.config.process_active_laws_only = False
    process = Mock(return_value=object())
    monkeypatch.setattr(pipeline, "process_document", process)
    assert pipeline.process_primary_acts(exclude_codes=True, limit=1) == 1
    process.assert_called_once_with("123/45", doc_type="laws")


def test_constitution_respects_skip_existing(pipeline, monkeypatch):
    from src.config import CONSTITUTION_NREG
    pipeline.processed_docs.add(CONSTITUTION_NREG.replace("/", "_"))
    fetch = Mock(side_effect=AssertionError("must not fetch"))
    monkeypatch.setattr(pipeline.api_client, "get_constitution", fetch)
    assert pipeline.process_constitution() is None
    assert pipeline.stats["skipped"] == 1


def test_treaty_only_fetch_does_not_request_domestic_list(pipeline, monkeypatch):
    fetch = Mock(return_value=Mock(content=b"treaty-id\n"))
    monkeypatch.setattr(pipeline.api_client, "_make_request", fetch)
    assert pipeline.api_client.get_primary_acts_list(True, False) == ["treaty-id"]
    assert fetch.call_count == 1
    assert fetch.call_args.args[0].endswith(pipeline.config.rada.primary_intl_list)


def test_canonical_orchestration_import():
    from src.pipelines.rada import LegalDocumentPipeline
    from src.cli.rada import main as cli_main
    assert callable(cli_main)
