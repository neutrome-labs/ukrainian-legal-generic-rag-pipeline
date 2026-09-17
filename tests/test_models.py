"""Shared documents safely encode YAML-compatible JSON values."""

import json

import yaml

from src.core.models import LegalDocument


def test_frontmatter_round_trip():
    title = 'Вирок № 1: "частина"\nдруга'
    document = LegalDocument(
        "00123", "Повний текст", title,
        {"source": "example", "types": [1, 2], "empty": None, "title": "wrong"},
    )
    header, body = document.to_markdown().split("---\n")[1:]
    fields = {key: json.loads(value) for key, value in
              (line.split(": ", 1) for line in header.strip().splitlines())}
    assert list(fields)[:2] == ["doc_id", "title"]
    assert fields["doc_id"] == "00123"
    assert fields["title"] == title
    assert fields["types"] == [1, 2]
    assert "empty" not in fields
    assert body == "Повний текст"
    assert yaml.safe_load(header) == fields


def test_hash_is_stable_and_changes_with_content():
    first = LegalDocument("a", "same", "t")
    assert first.content_hash == LegalDocument("a", "same", "t").content_hash
    assert first.content_hash != LegalDocument("a", "changed", "t").content_hash
