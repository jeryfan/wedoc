"""Notification request/response schemas.

Ports packages/openapi/src/notification and core notification.schema.ts.
"""

from enum import StrEnum

from pydantic import StrictBool

from ...core.validation import ZodEnumStr, ZodModel


class NotificationTypeEnum(StrEnum):
    SYSTEM = "system"
    COLLABORATOR_CELL_TAG = "collaboratorCellTag"
    COLLABORATOR_MULTI_ROW_TAG = "collaboratorMultiRowTag"
    COMMENT = "comment"
    EXPORT_BASE = "exportBase"
    ADMIN_NOTICE = "adminNotice"
    COLLABORATOR_INVITE = "collaboratorInvite"


class NotificationStatesEnum(StrEnum):
    UNREAD = "unread"
    READ = "read"


class NotificationSeverityEnum(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


_STATES = [e.value for e in NotificationStatesEnum]
_SEVERITIES = [e.value for e in NotificationSeverityEnum]
_TYPES = [e.value for e in NotificationTypeEnum]


class GetNotifyListQuery(ZodModel):
    notifyStates: ZodEnumStr(_STATES)
    severity: ZodEnumStr(_SEVERITIES) | None = None
    notifyType: ZodEnumStr(_TYPES) | None = None
    cursor: str | None = None


class UpdateNotifyStatusRo(ZodModel):
    isRead: StrictBool
