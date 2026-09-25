"""Mail-sender request schemas — ports packages/openapi/src/mail."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel, zod_email

_MAX_RECIPIENTS = 50


class MailTransportConfig(ZodModel):
    senderName: str | None = None
    sender: str
    host: str
    port: int
    secure: bool | None = None
    auth: dict[str, Any]


class TestMailTransportConfigRo(ZodModel):
    to: str
    message: str | None = None
    transportConfig: MailTransportConfig

    @field_validator("to")
    @classmethod
    def _to_email(cls, value: str) -> str:
        return zod_email(value)


def _to_email_array(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    return value if isinstance(value, list) else [value]


class SendEmailRo(ZodModel):
    to: str | list[str] | None = None
    subject: str
    body: str
    cc: str | list[str] | None = None
    bcc: str | list[str] | None = None
    replyTo: str | None = None
    smtp: MailTransportConfig | None = None
    bodyType: ZodEnumStr(["markdown", "html"]) = "markdown"

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _emails(cls, value: str | list[str] | None) -> str | list[str] | None:
        for email in _to_email_array(value):
            zod_email(email)
        return value

    @field_validator("replyTo")
    @classmethod
    def _reply_to(cls, value: str | None) -> str | None:
        if value is not None:
            zod_email(value)
        return value


def refine_send_email(data: SendEmailRo) -> None:
    """zod `.refine()` rules run after the object parses; path-anchored issues."""
    from ...core.errors import ApiError, HttpErrorCode

    issues: list[tuple[str, str | None]] = []
    to_n = len(_to_email_array(data.to))
    cc_n = len(_to_email_array(data.cc))
    bcc_n = len(_to_email_array(data.bcc))
    if to_n == 0 and bcc_n == 0:
        issues.append(('Either "to" or "bcc" must be provided', "to"))
    if to_n + cc_n + bcc_n > _MAX_RECIPIENTS:
        issues.append(
            (
                f"A maximum of {_MAX_RECIPIENTS} recipients (to + cc + bcc) is allowed per request",
                None,
            )
        )
    if not issues:
        return
    parts = []
    for message, path in issues:
        text = message[0].upper() + message[1:] if message else message
        parts.append(text + (f' at "{path}"' if path else ""))
    raise ApiError("Validation error: " + "; ".join(parts), HttpErrorCode.VALIDATION_ERROR)
