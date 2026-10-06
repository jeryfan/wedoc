"""Attachment request schemas — ports packages/openapi/src/attachment."""

from enum import IntEnum

from ...core.validation import ZodEnumInt, ZodModel


class UploadType(IntEnum):
    TABLE = 1
    AVATAR = 2
    FORM = 3
    OAUTH = 4
    IMPORT = 5
    PLUGIN = 6
    COMMENT = 7
    LOGO = 8
    EXPORT_BASE = 9
    TEMPLATE = 10
    CHAT_DATA_VISUALIZATION_CODE = 11
    APP = 12
    CHAT_FILE = 13
    AUTOMATION = 14
    RECORD_HISTORY = 15
    SPACE_AVATAR = 16
    RECORD_REMOVAL = 17
    WORKFLOW_RUN_COLD = 18
    AUDIT_LOG_COLD = 19
    ARTIFACT = 20


_DIR = {
    UploadType.TABLE: "table",
    UploadType.AVATAR: "avatar",
    UploadType.FORM: "form",
    UploadType.OAUTH: "oauth",
    UploadType.IMPORT: "import",
    UploadType.PLUGIN: "plugin",
    UploadType.COMMENT: "comment",
    UploadType.LOGO: "logo",
    UploadType.EXPORT_BASE: "export-base",
    UploadType.TEMPLATE: "template",
    UploadType.CHAT_DATA_VISUALIZATION_CODE: "chat-data-visualization-code",
    UploadType.APP: "app",
    UploadType.CHAT_FILE: "chat-file",
    UploadType.AUTOMATION: "automation",
    UploadType.RECORD_HISTORY: "record-history",
    UploadType.SPACE_AVATAR: "space-avatar",
    UploadType.RECORD_REMOVAL: "record-removal",
    UploadType.WORKFLOW_RUN_COLD: "workflow-run",
    UploadType.AUDIT_LOG_COLD: "audit-log",
    UploadType.ARTIFACT: "artifact",
}

_PUBLIC_TYPES = {
    UploadType.AVATAR,
    UploadType.OAUTH,
    UploadType.FORM,
    UploadType.PLUGIN,
    UploadType.LOGO,
    UploadType.TEMPLATE,
    UploadType.CHAT_DATA_VISUALIZATION_CODE,
    UploadType.SPACE_AVATAR,
}

# cold-archive parts are written only by backend flushers, never client-signed
_BACKEND_ONLY_TYPES = {
    UploadType.RECORD_HISTORY,
    UploadType.RECORD_REMOVAL,
    UploadType.WORKFLOW_RUN_COLD,
    UploadType.AUDIT_LOG_COLD,
    UploadType.ARTIFACT,
}

_IMMUTABLE_CACHE_TYPES = {
    UploadType.TEMPLATE,
    UploadType.FORM,
    UploadType.OAUTH,
    UploadType.CHAT_DATA_VISUALIZATION_CODE,
}
_SHORT_CACHE_TYPES = {
    UploadType.AVATAR,
    UploadType.SPACE_AVATAR,
    UploadType.LOGO,
    UploadType.PLUGIN,
}


def upload_dir(upload_type: int) -> str:
    return _DIR[UploadType(upload_type)]


def upload_bucket(upload_type: int, public_bucket: str, private_bucket: str) -> str:
    return public_bucket if UploadType(upload_type) in _PUBLIC_TYPES else private_bucket


def is_public_bucket(bucket: str, public_bucket: str) -> bool:
    return bucket == public_bucket


def is_backend_only(upload_type: int) -> bool:
    return UploadType(upload_type) in _BACKEND_ONLY_TYPES


def cache_control(upload_type: int) -> str | None:
    t = UploadType(upload_type)
    if t in _IMMUTABLE_CACHE_TYPES:
        return "public, max-age=31536000, immutable"
    if t in _SHORT_CACHE_TYPES:
        return "public, max-age=3600"
    return None


# z.nativeEnum(UploadType): a numeric enum, so options list unquoted (1|2|...|20)
UploadTypeInt = ZodEnumInt([t.value for t in UploadType])


class SignatureRo(ZodModel):
    contentType: str
    contentLength: int
    expiresIn: int | None = None
    type: UploadTypeInt
    baseId: str | None = None
