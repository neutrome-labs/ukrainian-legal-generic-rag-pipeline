"""Source-independent document passed to storage and AI Search."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class LegalDocument:
    """One complete source document; segmentation is left to AI Search."""

    doc_id: str
    content: str
    title: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.md5(self.to_markdown().encode("utf-8")).hexdigest()[:12]

    def to_markdown(self) -> str:
        # JSON values are valid YAML and safely escape quotes, newlines and lists.
        fields = {**self.metadata, "doc_id": self.doc_id, "title": self.title}
        fields = {"doc_id": fields.pop("doc_id"), "title": fields.pop("title"), **fields}
        frontmatter = ["---"]
        for key, value in fields.items():
            if value is not None:
                encoded_key = key if key.replace("_", "").isalnum() else json.dumps(key)
                frontmatter.append(f"{encoded_key}: {json.dumps(value, ensure_ascii=False)}")
        return "\n".join([*frontmatter, "---", ""]) + self.content
