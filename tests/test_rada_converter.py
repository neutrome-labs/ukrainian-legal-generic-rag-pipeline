"""Converter and Rada storage keys, via canonical modules."""

from src.sources.rada.converter import MarkdownConverter
from src.storage import LocalStorage


def test_converter_preserves_a_complete_document():
    document = MarkdownConverter().process_document(
        doc_id="test-doc",
        title="Тестовий документ",
        text="Стаття 1. Перша норма.\n\nСтаття 2. Друга норма.",
        metadata={"status": "active"},
    )

    assert document.doc_id == "test-doc"
    assert "## Стаття 1" in document.content
    assert "## Стаття 2" in document.content
    assert 'title: "Тестовий документ"' in document.to_markdown()


def test_local_storage_uses_one_file_per_document(tmp_path):
    document = MarkdownConverter().process_document("123-45", "Тест", "Повний текст")

    result = LocalStorage(str(tmp_path)).upload_document(document)

    assert result.success
    assert result.key == "laws/123-45.md"
