"""Load complete decisions from official annual TSV directories and linked RTFs."""

from pathlib import Path
from typing import Iterator

from ...core.models import LegalDocument
from .export import AnnualExport
from .texts import DecisionTextLoader


class EdrsrExportSource:
    source_id = "edrsr"
    document_type = "decisions"

    def __init__(
        self, directories: str | Path | list[str | Path], loader: DecisionTextLoader,
        limit: int | None = None, skip_ids: set[str] | None = None,
    ):
        self.directories = [directories] if isinstance(directories, (str, Path)) else directories
        self.loader = loader
        self.limit = limit
        self.skip_ids = skip_ids or set()
        self.stats = {}
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")

    def iter_records(self) -> Iterator[dict]:
        """Limit is input rows across all directories, before publication/ID filtering."""
        self.stats = {"rows_read": 0, "non_public": 0, "existing": 0}
        if self.limit == 0:
            return
        for directory in self.directories:
            records = AnnualExport(directory).iter_records()
            try:
                for row in records:
                    self.stats["rows_read"] += 1
                    yield row
                    if self.limit is not None and self.stats["rows_read"] >= self.limit:
                        return
            finally:
                records.close()

    def iter_documents(self) -> Iterator[LegalDocument]:
        records = self.iter_records()
        try:
            for row in records:
                # Fail closed: never retrieve non-public/unknown statuses, even from cache.
                if row["status"] != "1":
                    self.stats["non_public"] += 1
                    continue
                if row["doc_id"] in self.skip_ids:
                    self.stats["existing"] += 1
                    continue
                text = self.loader.load(row["doc_url"])
                title = f"{row['judgment_name'] or 'Судове рішення'} у справі № {row['cause_num']}"
                yield LegalDocument(
                    doc_id=row["doc_id"], title=title, content=text,
                    metadata={
                        **{key: value for key, value in row.items() if key != "doc_id"},
                        "source": "reyestr.court.gov.ua", "source_id": "edrsr",
                        "source_url": f"https://reyestr.court.gov.ua/Review/{row['doc_id']}",
                        "decision_id": row["doc_id"], "case_number": row["cause_num"],
                        "document_type": "court_decision", "language": "uk",
                    },
                )
        finally:
            records.close()
