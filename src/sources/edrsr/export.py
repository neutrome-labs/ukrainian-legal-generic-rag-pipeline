"""Streaming reader for official ЄДРСР tab-separated annual exports."""

import csv
import json
import re
from pathlib import Path
from typing import Iterator

DOCUMENT_COLUMNS = {
    "doc_id", "court_code", "judgment_code", "justice_kind", "category_code",
    "cause_num", "adjudication_date", "receipt_date", "judge", "doc_url", "status", "date_publ",
}
LOOKUPS = {
    "courts": ("court_code", {"court_code", "name", "instance_code", "region_code"}),
    "instances": ("instance_code", {"instance_code", "name"}),
    "regions": ("region_code", {"region_code", "name"}),
    "judgment_forms": ("judgment_code", {"judgment_code", "name"}),
    "justice_kinds": ("justice_kind", {"justice_kind", "name"}),
    "cause_categories": ("category_code", {"category_code", "name"}),
}


def read_rows(path: Path, required: set[str]) -> Iterator[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}:{reader.line_num}: malformed TSV row")
            yield row


class AnnualExport:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def iter_records(self) -> Iterator[dict]:
        provenance_path = self.directory / "_source.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
        attribution = {key: provenance[key] for key in ("dataset_id", "dataset_url", "resource_id", "license_id", "archive_sha256") if key in provenance}
        lookups = {
            name: {row[key]: row for row in read_rows(self.directory / f"{name}.csv", columns)}
            for name, (key, columns) in LOOKUPS.items()
        }
        for row in read_rows(self.directory / "documents.csv", DOCUMENT_COLUMNS):
            if not re.fullmatch(r"[0-9]+", row["doc_id"]):
                raise ValueError(f"{self.directory}/documents.csv: invalid doc_id {row['doc_id']!r}")
            court = lookups["courts"].get(row["court_code"], {})
            yield {
                **row,
                **attribution,
                "court_name": court.get("name"),
                "instance_code": court.get("instance_code"),
                "instance_name": lookups["instances"].get(court.get("instance_code"), {}).get("name"),
                "region_code": court.get("region_code"),
                "region_name": lookups["regions"].get(court.get("region_code"), {}).get("name"),
                "judgment_name": lookups["judgment_forms"].get(row["judgment_code"], {}).get("name"),
                "justice_kind_name": lookups["justice_kinds"].get(row["justice_kind"], {}).get("name"),
                "category_name": lookups["cause_categories"].get(row["category_code"], {}).get("name"),
                "export_directory": self.directory.name,
            }
