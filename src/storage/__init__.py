"""Source-independent storage adapters and factory."""

from typing import Optional

from ..config import R2Config
from .base import Storage, UploadResult
from .local import LocalStorage
from .r2 import R2Uploader


def get_uploader(
    use_local: bool = False,
    output_dir: str = "./output",
    config: Optional[R2Config] = None,
    namespace: str = "",
) -> R2Uploader | LocalStorage:
    return LocalStorage(output_dir, namespace=namespace) if use_local else R2Uploader(config, namespace=namespace)


__all__ = ["Storage", "UploadResult", "LocalStorage", "R2Uploader", "get_uploader"]
