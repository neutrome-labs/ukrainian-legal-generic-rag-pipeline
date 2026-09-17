"""Minimal contract for sources that yield normalized complete documents."""

from typing import Iterator, Protocol

from .models import LegalDocument


class DocumentSource(Protocol):
    source_id: str
    document_type: str

    def iter_documents(self) -> Iterator[LegalDocument]: ...
