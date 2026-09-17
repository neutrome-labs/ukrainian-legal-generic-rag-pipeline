"""CLI defaults, negative switches and side-effect-free previews."""

import argparse
import json
from unittest.mock import Mock

import pytest

from src.cli import edrsr, rada


@pytest.mark.parametrize("module, argv, local", [(rada, [], False), (edrsr, ["2025"], True)])
def test_storage_choices(module, argv, local):
    parser = module.build_parser()
    assert parser.parse_args(argv).local is local
    assert parser.parse_args([*argv, "--local"]).local is True
    assert parser.parse_args([*argv, "--remote"]).local is False
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--local", "--remote"])


@pytest.mark.parametrize("module, argv", [(rada, []), (edrsr, ["2025"])])
def test_every_boolean_can_be_enabled_and_disabled(module, argv):
    parser = module.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse.BooleanOptionalAction):
            positive, negative = action.option_strings
            assert getattr(parser.parse_args([*argv, positive]), action.dest) is True
            assert getattr(parser.parse_args([*argv, negative]), action.dest) is False
            assert getattr(parser.parse_args([*argv, positive, negative]), action.dest) is False


def test_rada_defaults():
    args = rada.build_parser().parse_args([])
    assert args.include_constitution and args.include_codes and args.include_laws
    assert not args.include_international
    assert args.active_only and args.generate_index
    assert not args.recent and not args.skip_existing and not args.debug and not args.dry_run
    assert args.workers == 4 and args.limit is None and args.pages == 1


def test_rada_dry_run_has_no_side_effects(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    constructor = Mock(side_effect=AssertionError("must not initialize pipeline"))
    monkeypatch.setattr(rada, "LegalDocumentPipeline", constructor)
    monkeypatch.setattr(rada, "load_config", Mock(side_effect=AssertionError("must not load config")))
    rada.main(["--dry-run", "--remote", "--no-include-constitution"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["storage"] == "remote"
    assert plan["options"]["include_constitution"] is False
    assert list(tmp_path.iterdir()) == []


def test_rada_wires_stage_switches(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pipeline = Mock()
    pipeline.run_full_pipeline.return_value = {}
    constructor = Mock(return_value=pipeline)
    monkeypatch.setattr(rada, "LegalDocumentPipeline", constructor)
    config = Mock()
    monkeypatch.setattr(rada, "load_config", Mock(return_value=config))
    rada.main(["--local", "--no-include-constitution", "--no-include-codes", "--no-include-laws",
               "--include-international", "--no-generate-index", "--no-active-only",
               "--workers", "2", "--skip-existing"])
    pipeline.run_full_pipeline.assert_called_once_with(include_constitution=False, include_codes=False,
                                                       include_laws=False, include_international=True,
                                                       limit=None, max_workers=2)
    pipeline.generate_index.assert_not_called()
    assert config.process_active_laws_only is False
    assert constructor.call_args.kwargs["use_local_storage"] is True
    assert constructor.call_args.kwargs["skip_existing"] is True


@pytest.mark.parametrize("flags", [["--workers", "0"], ["--limit", "-1"], ["--pages", "0"]])
def test_rada_invalid_values(flags):
    with pytest.raises(SystemExit) as error:
        rada.main([*flags, "--dry-run"])
    assert error.value.code == 2


def test_edrsr_dry_run_reads_only_metadata(monkeypatch, tmp_path, capsys):
    source = Mock()
    source.iter_records.return_value = [{"doc_id": "1"}]
    factory = Mock(return_value=source)
    monkeypatch.setattr(edrsr, "EdrsrExportSource", factory)
    loader = Mock()
    monkeypatch.setattr(edrsr, "DecisionTextLoader", Mock(return_value=loader))
    storage = Mock(side_effect=AssertionError("must not access storage"))
    monkeypatch.setattr(edrsr, "get_uploader", storage)
    root = tmp_path / "cache"
    edrsr.main(["2025", "--dry-run", "--remote", "--cache-dir", str(root)])
    assert json.loads(capsys.readouterr().out) == {"doc_id": "1"}
    assert factory.call_args.kwargs["limit"] == 10
    loader.load.assert_not_called()
    loader.close.assert_called_once()
    source.iter_documents.assert_not_called()
    storage.assert_not_called()
    assert not root.exists()


@pytest.mark.parametrize("flag, local", [("--remote", False), ("--local", True)])
def test_edrsr_storage_wiring(monkeypatch, tmp_path, flag, local):
    source = Mock(stats={})
    monkeypatch.setattr(edrsr, "EdrsrExportSource", Mock(return_value=source))
    monkeypatch.setattr(edrsr, "DecisionTextLoader", Mock())
    storage = Mock()
    factory = Mock(return_value=storage)
    monkeypatch.setattr(edrsr, "get_uploader", factory)
    pipeline = Mock()
    pipeline.run.return_value = {"errors": 0}
    monkeypatch.setattr(edrsr, "ImportPipeline", Mock(return_value=pipeline))
    edrsr.main(["2025", flag, "--no-dry-run", "--no-skip-existing", "--cache-dir", str(tmp_path)])
    assert factory.call_args.kwargs["use_local"] is local
    storage.get_processed_doc_ids.assert_not_called()
    pipeline.run.assert_called_once_with(skip_existing=False)
