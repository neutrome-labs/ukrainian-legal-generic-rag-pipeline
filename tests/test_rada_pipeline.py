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


def test_canonical_orchestration_import():
    from src.pipelines.rada import LegalDocumentPipeline
    from src.cli.rada import main as cli_main
    assert callable(cli_main)
