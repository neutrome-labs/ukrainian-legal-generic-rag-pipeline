"""Official TSV schema with tiny synthetic annual exports; no network required."""

import csv
import hashlib
from unittest.mock import Mock

import pytest

from src.pipelines.import_documents import ImportPipeline
from src.sources.edrsr.export import AnnualExport, DOCUMENT_COLUMNS, LOOKUPS
from src.sources.edrsr.importer import EdrsrExportSource
from src.sources.edrsr.texts import DecisionTextLoader
from src.storage import LocalStorage, UploadResult

URL = "https://od.reyestr.court.gov.ua/files/62/test.rtf"


def write_tsv(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def export(tmp_path):
    directory = tmp_path / "edrsr-data-2025"
    directory.mkdir()
    for name, (key, columns) in LOOKUPS.items():
        row = {column: "01" for column in columns}
        row.update({key: "01", "name": "Тестовий суд" if name == "courts" else "Тест"})
        write_tsv(directory / f"{name}.csv", sorted(columns), [row])
    row = {column: "01" for column in DOCUMENT_COLUMNS}
    row.update(doc_id="100000001", cause_num="test/1/26", doc_url=URL, status="1")
    rows = [row, {**row, "doc_id": "100000002", "status": "0"}]
    write_tsv(directory / "documents.csv", sorted(DOCUMENT_COLUMNS), rows)
    return directory


def test_real_schema_joins_and_leading_zeros(export):
    record = next(AnnualExport(export).iter_records())
    assert record["court_name"] == "Тестовий суд"
    assert record["region_code"] == "01"
    assert record["cause_num"] == "test/1/26"
    assert record["export_directory"] == "edrsr-data-2025"


def test_import_to_local_and_resume(export, tmp_path):
    loader = Mock()
    loader.load.return_value = "ОСОБА_1\nПовний текст"
    source = EdrsrExportSource(export, loader)
    storage = LocalStorage(str(tmp_path / "output"), namespace="edrsr")
    assert ImportPipeline(source, storage).run()["processed"] == 1
    assert source.stats == {"rows_read": 2, "non_public": 1, "existing": 0}
    loader.load.assert_called_once_with(URL)
    markdown = (storage.output_dir / "edrsr/decisions/100000001.md").read_text()
    assert "ОСОБА_1" in markdown
    assert 'source_id: "edrsr"' in markdown
    assert "data.rada.gov.ua" not in markdown
    source.skip_ids = storage.get_processed_doc_ids()
    loader.reset_mock()
    assert ImportPipeline(source, storage).run()["processed"] == 0
    loader.load.assert_not_called()
    assert source.stats["existing"] == 1
    assert LocalStorage(str(storage.output_dir)).get_processed_doc_ids() == set()
    storage.upload_index([])
    assert storage.get_processed_doc_ids() == {"100000001"}


def test_multiple_directories_and_global_limit(export):
    source = EdrsrExportSource([export, export], Mock(), limit=3)
    assert len(list(source.iter_records())) == 3


def test_zero_limit_does_not_open_files():
    source = EdrsrExportSource("missing-directory", Mock(), limit=0)
    assert list(source.iter_documents()) == []


def test_missing_header_rejected(export):
    (export / "documents.csv").write_text("doc_id\n123\n")
    with pytest.raises(ValueError, match="missing columns"):
        list(AnnualExport(export).iter_records())


@pytest.mark.parametrize("method", ["upload_document", "upload_metadata"])
def test_failed_write_does_not_mark_processed(export, tmp_path, monkeypatch, method):
    storage = LocalStorage(str(tmp_path / "output"), namespace="edrsr")
    monkeypatch.setattr(storage, method, Mock(return_value=UploadResult(False, "key")))
    loader = Mock()
    loader.load.return_value = "Текст"
    assert ImportPipeline(EdrsrExportSource(export, loader), storage).run()["errors"] == 1
    assert storage.get_processed_doc_ids() == set()


@pytest.mark.parametrize("data", [
    b"{\\rtf1\\ansi\\ansicpg1251 " + "Текст".encode("cp1251") + b"}",
    b"{\\rtf1\\ansi\\ansicpg1251 \\'d2\\'e5\\'ea\\'f1\\'f2}",
    b"{\\rtf1\\ansi\\uc1 \\u1058?\\u1077?\\u1082?\\u1089?\\u1090?}",
])
def test_rtf_cyrillic(data):
    assert DecisionTextLoader.convert(data, "rtf") == "Текст"


def test_reject_access_page():
    with pytest.raises(ValueError, match="Expected RTF"):
        DecisionTextLoader.convert(b"<html>Access denied</html>", "rtf")


def test_offline_cache(tmp_path):
    loader = DecisionTextLoader(tmp_path, offline=True)
    with pytest.raises(ValueError, match="not cached"):
        loader.load(URL)
    key = hashlib.sha256(URL.encode()).hexdigest()
    path = tmp_path / key[:2] / f"{key}.rtf"
    path.parent.mkdir()
    path.write_bytes(b"{\\rtf1 cached text}")
    assert loader.load(URL) == "cached text"
    loader.close()


@pytest.mark.parametrize("url", ["http://od.reyestr.court.gov.ua/files/a.rtf", "https://example.com/files/a.rtf",
                                  "https://od.reyestr.court.gov.ua/Review/123"])
def test_unsupported_url_rejected(tmp_path, url):
    loader = DecisionTextLoader(tmp_path, offline=True)
    with pytest.raises(ValueError, match="Unsupported document URL"):
        loader.load(url)
    loader.close()


def test_legacy_html_declared_windows_1251():
    page = (b"<HTML><HEAD><TITLE>19/273</TITLE>"
            b"<META NAME=\"JUDGENAME1\" CONTENT=\"\xc1\xee\xff\xf7\xea\xee\">"
            b"<META HTTP-EQUIV=\"Content-Type\" CONTENT=\"text/html; charset=windows-1251\"></HEAD>"
            b"<BODY><P>1.&nbsp;</P><P class=ps1>" + "Ухвала суду".encode("cp1251") + b"<BR>\xcd\xee\xf0\xec\xe0</P></BODY></HTML>")
    text = DecisionTextLoader.convert(page, "html")
    assert "Ухвала суду" in text
    assert "Норма" in text
    assert "БОЙЧЕНКО" not in text
    with pytest.raises(ValueError, match="no readable text"):
        DecisionTextLoader.convert(b"<html><head><meta charset=windows-1251></head><body></body></html>", "html")
    with pytest.raises(ValueError, match="Unsupported declared charset"):
        DecisionTextLoader.convert(b"<meta charset=koi8-u><body>x</body>", "html")


def test_html_charset_crosses_old_2048_byte_boundary(tmp_path):
    # The old parser's data[:2048] ends at exactly "charset=windows-".
    head = b'<html><head>'
    declaration = b'<meta http-equiv="Content-Type" content="text/html; charset='
    prefix = head + b' ' * (2040 - len(head) - len(declaration)) + declaration
    data = prefix + b'windows-1251"></head><body>' + 'Ухвала суду'.encode('cp1251') + b'</body></html>'
    assert data[:2048].endswith(b'charset=windows-')
    assert DecisionTextLoader.convert(data, 'html') == 'Ухвала суду'
    url = URL.removesuffix('.rtf') + '.html'
    key = hashlib.sha256(url.encode()).hexdigest()
    path = tmp_path / key[:2] / f'{key}.html'
    path.parent.mkdir()
    path.write_bytes(data)
    loader = DecisionTextLoader(tmp_path, offline=True)
    try:
        assert loader.load(url) == 'Ухвала суду'
    finally:
        loader.close()


@pytest.mark.parametrize('declaration', [
    b'<META CHARSET = "UTF-8">',
    b"<meta charset='utf-8'>",
    b'<meta http-equiv="Content-Type" content="text/html; CHARSET = utf-8">',
])
def test_html_late_quoted_case_insensitive_charset(declaration):
    data = (b'<html><head>' + b' ' * 4096 + declaration + b'</head><body>'
            + 'Український текст'.encode('utf-8') + b'</body></html>')
    assert DecisionTextLoader.convert(data, 'html') == 'Український текст'


@pytest.mark.parametrize('charset', [b'koi8-u', b'windows-'])
def test_html_late_unsupported_charset_still_rejected(charset):
    data = b'<head>' + b' ' * 4096 + b'<meta charset="' + charset + b'"></head><body>text</body>'
    with pytest.raises(ValueError, match='Unsupported declared charset'):
        DecisionTextLoader.convert(data, 'html')


def test_html_ignores_charset_text_outside_meta():
    data = b'<html><body>charset=koi8-u is just text</body></html>'
    assert DecisionTextLoader.convert(data, 'html') == 'charset=koi8-u is just text'


def test_mismatched_extension_rejected(tmp_path):
    loader = DecisionTextLoader(tmp_path, offline=True)
    with pytest.raises(ValueError, match="Expected RTF"):
        loader.convert(b"<html>not rtf</html>", "rtf")
    loader.close()


def test_download_cached_once(tmp_path, caplog):
    loader = DecisionTextLoader(tmp_path, delay=0)
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"{\\rtf1 downloaded text}"]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    loader.session.get = Mock(return_value=response)
    with caplog.at_level("INFO", logger="src.sources.edrsr.texts"):
        assert loader.load(URL) == "downloaded text"
        assert loader.load(URL) == "downloaded text"
    assert f"Downloading text: {URL}" in caplog.text
    assert "Downloaded" in caplog.text
    assert f"Reading cached text: {URL}" in caplog.text
    loader.session.get.assert_called_once()
    loader.close()


def test_download_wait_is_reported(monkeypatch, tmp_path, caplog):
    from src.sources.edrsr import texts
    loader = DecisionTextLoader(tmp_path, delay=6)
    loader.last_request = 10
    monkeypatch.setattr(texts.time, "monotonic", lambda: 11)
    sleep = Mock()
    monkeypatch.setattr(texts.time, "sleep", sleep)
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"{\\rtf1 downloaded text}"]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    loader.session.get = Mock(return_value=response)
    with caplog.at_level("INFO", logger="src.sources.edrsr.texts"):
        assert loader.load(URL) == "downloaded text"
    sleep.assert_called_once_with(5)
    assert "Waiting 5.0s before next download" in caplog.text
    loader.close()


@pytest.mark.parametrize("namespace", ["..", "a/b", "a\\b"])
def test_bad_namespace_rejected(tmp_path, namespace):
    with pytest.raises(ValueError):
        LocalStorage(str(tmp_path), namespace=namespace)
