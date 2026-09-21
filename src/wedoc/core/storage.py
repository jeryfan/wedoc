"""Attachment storage: local provider + public URL contract.

Ports the slices of the upstream attachments plugin stack that the avatar
chain needs (attachments module extends this in M4):

- ``LocalStorage``: objects under ``{BACKEND_STORAGE_LOCAL_PATH}/{bucket}/{path}``,
  sha256 content hash, identical layout to the upstream local plugin
- ``get_public_full_storage_url``: the prefix rules of
  openapi ``getPublicFullStorageUrl`` (publicUrl > minio > s3 > local read path)
- ``storage_token_encryptor``: the AES-CBC expire-token codec used by the
  local provider's read/upload urls (key/iv from ``config.storage_cipher``)
"""

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Settings, get_settings
from .errors import ApiError, HttpErrorCode

if TYPE_CHECKING:
    from .security.auth import Encryptor


def _encryptor_class():
    # Lazy: wedoc.core.security.auth imports this module for the public-url
    # helper, and Encryptor lives in auth — a top-level import would cycle.
    from .security.auth import Encryptor

    return Encryptor


READ_PATH = "/api/attachments/read"

PUBLIC_BUCKET_CACHE_CONTROL = "public, max-age=3600"


def path_join(*parts: str) -> str:
    """openapi pathJoin(): join with '/', collapse duplicate separators."""
    joined = "/".join(parts)
    out: list[str] = []
    for ch in joined:
        if ch == "/" and out and out[-1] == "/":
            continue
        out.append(ch)
    return "".join(out)


def get_public_full_storage_url(path: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.backend_storage_public_url:
        return settings.backend_storage_public_url + path_join("/", path)
    prefix = settings.storage_url_prefix
    provider = settings.backend_storage_provider
    if provider == "minio":
        return prefix + path_join("/", settings.backend_storage_public_bucket, path)
    if provider in ("s3", "aliyun"):
        return prefix + path_join("/", path)
    return prefix + path_join(READ_PATH, settings.backend_storage_public_bucket, path)


def _storage_cipher_entries(settings: Settings) -> list[tuple[str, bytes, bytes]]:
    algorithm = settings.backend_storage_encryption_algorithm
    key, iv = settings.storage_cipher
    entries = [(algorithm, key.encode(), iv.encode())]
    old_key = settings.backend_storage_encryption_key_old
    old_iv = settings.backend_storage_encryption_iv_old
    if bool(old_key) != bool(old_iv):
        raise ValueError(
            "BACKEND_STORAGE_ENCRYPTION_KEY_OLD and "
            "BACKEND_STORAGE_ENCRYPTION_IV_OLD must be set together"
        )
    if old_key and old_iv:
        old_entry = (algorithm, old_key.encode(), old_iv.encode())
        if old_entry != entries[0]:
            entries.append(old_entry)
    return entries


def storage_token_encryptor(settings: Settings | None = None) -> Encryptor:
    """Encryptor over {expiresDate, respHeaders} tokens (local provider urls)."""
    entries = _storage_cipher_entries(settings or get_settings())
    return _encryptor_class()(entries, encoding="base64")


class LocalStorage:
    """Upstream LocalStorage plugin port (filesystem rooted at local path)."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self._settings = settings
        self.root = Path(settings.backend_storage_local_path).resolve()
        os.makedirs(self.root, exist_ok=True)

    def _resolve(self, bucket: str, path: str) -> Path:
        target = (self.root / bucket / path).resolve()
        if not str(target).startswith(str(self.root)):
            raise ApiError("Invalid path", HttpErrorCode.VALIDATION_ERROR)
        return target

    def upload_file(self, bucket: str, path: str, data: bytes) -> dict[str, str | int]:
        target = self._resolve(bucket, path)
        os.makedirs(target.parent, exist_ok=True)
        target.write_bytes(data)
        return {"hash": hashlib.sha256(data).hexdigest(), "path": path, "size": len(data)}

    def read_file(self, bucket: str, path: str) -> bytes | None:
        target = self._resolve(bucket, path)
        if not target.is_file():
            return None
        return target.read_bytes()

    def delete_file(self, bucket: str, path: str) -> None:
        target = self._resolve(bucket, path)
        target.unlink(missing_ok=True)


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
