import unittest

from src.markdown_converter import MarkdownConverter
from src.r2_uploader import LocalStorage


class DocumentPipelineTests(unittest.TestCase):
    def test_converter_preserves_a_complete_document(self):
        document = MarkdownConverter().process_document(
            doc_id="test-doc",
            title="Тестовий документ",
            text="Стаття 1. Перша норма.\n\nСтаття 2. Друга норма.",
            metadata={"status": "active"},
        )

        self.assertEqual(document.doc_id, "test-doc")
        self.assertIn("## Стаття 1", document.content)
        self.assertIn("## Стаття 2", document.content)
        self.assertIn('title: "Тестовий документ"', document.to_markdown())

    def test_local_storage_uses_one_file_per_document(self):
        import tempfile

        document = MarkdownConverter().process_document("123-45", "Тест", "Повний текст")
        with tempfile.TemporaryDirectory() as directory:
            result = LocalStorage(directory).upload_document(document)
            self.assertTrue(result.success)
            self.assertEqual(result.key, "laws/123-45.md")


if __name__ == "__main__":
    unittest.main()
