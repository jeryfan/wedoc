"""Request schemas for /api/import — ports packages/openapi/src/import."""

from typing import Any
from urllib.parse import urlparse

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel

SupportedTypeStr = ZodEnumStr(["csv", "excel"])

READ_PATH = "/api/attachments/read"


def _validate_attachment_url(cls: Any, value: str) -> str:
    trimmed = value.strip()
    parsed = urlparse(trimmed)
    if parsed.scheme in ("http", "https"):
        return value
    if trimmed.startswith(f"{READ_PATH}/"):
        return value
    raise ValueError("attachmentUrl must be an http(s) URL or an attachment read path")


class AnalyzeRo(ZodModel):
    attachmentUrl: str
    fileType: SupportedTypeStr

    _v_url = field_validator("attachmentUrl")(classmethod(_validate_attachment_url))


class ImportColumn(ZodModel):
    type: str
    name: str
    sourceColumnIndex: int


class ImportSheetItem(ZodModel):
    name: str
    columns: list[ImportColumn]
    useFirstRowAsHeader: bool
    importData: bool


class ImportOptionRo(ZodModel):
    worksheets: dict[str, ImportSheetItem]
    attachmentUrl: str
    fileType: SupportedTypeStr
    tz: str
    notification: bool | None = None
    folderId: str | None = None

    _v_url = field_validator("attachmentUrl")(classmethod(_validate_attachment_url))


class InplaceInsertConfig(ZodModel):
    sourceWorkSheetKey: str
    excludeFirstRow: bool
    sourceColumnMap: dict[str, int | None]


class InplaceImportOptionRo(ZodModel):
    attachmentUrl: str
    fileType: SupportedTypeStr
    insertConfig: InplaceInsertConfig
    notification: bool | None = None

    _v_url = field_validator("attachmentUrl")(classmethod(_validate_attachment_url))


def analyze_column(type_: str, name: str) -> dict[str, Any]:
    return {"type": type_, "name": name}
