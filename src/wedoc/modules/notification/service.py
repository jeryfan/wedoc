"""Notification domain service — ports features/notification/notification.service.ts.

Read/mark-status paths only. The write paths (sendCommonNotify etc.) are driven
by internal events (comment mentions, collaborator tags) and land with those
modules; this service covers the four public REST routes.
"""

from datetime import UTC, datetime
from typing import Any

from ...core.ids import IdPrefix, new_id
from ...core.storage import get_public_full_storage_url
from . import repository
from .schemas import (
    GetNotifyListQuery,
    NotificationSeverityEnum,
    NotificationStatesEnum,
    NotificationTypeEnum,
    UpdateNotifyStatusRo,
)

_SYSTEM_ICON = {"iconUrl": "/images/favicon/favicon.svg"}
_SYSTEM_ICON_TYPES = {
    NotificationTypeEnum.SYSTEM,
    NotificationTypeEnum.EXPORT_BASE,
    NotificationTypeEnum.ADMIN_NOTICE,
}
_USER_ICON_TYPES = {
    NotificationTypeEnum.COMMENT,
    NotificationTypeEnum.COLLABORATOR_CELL_TAG,
    NotificationTypeEnum.COLLABORATOR_MULTI_ROW_TAG,
    NotificationTypeEnum.COLLABORATOR_INVITE,
}


def _generate_notify_icon(
    notify_type: str,
    from_user_id: str,
    from_user_sets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if notify_type in {e.value for e in _SYSTEM_ICON_TYPES}:
        return dict(_SYSTEM_ICON)
    user = from_user_sets[from_user_id]
    avatar = user.get("avatar")
    return {
        "userId": user["id"],
        "userName": user["name"],
        "userAvatarUrl": get_public_full_storage_url(avatar) if avatar else avatar,
    }


def _notification_severity(notify_type: str, severity: str | None = None) -> str:
    if severity and severity in {e.value for e in NotificationSeverityEnum}:
        return severity
    return NotificationSeverityEnum.INFO.value


class NotificationService:
    async def get_notify_list(self, user_id: str, query: GetNotifyListQuery) -> dict[str, Any]:
        is_read = query.notifyStates == NotificationStatesEnum.READ.value
        severity = query.severity
        notify_type = query.notifyType

        rows = await repository.list_notification_rows(
            user_id, is_read, severity, notify_type, query.cursor
        )
        summary = await repository.summary_by_severity(user_id, is_read)

        page = rows[: repository.NOTIFICATION_LIST_LIMIT]
        next_cursor = (
            page[-1]["id"] if len(rows) > repository.NOTIFICATION_LIST_LIMIT and page else None
        )

        from_user_ids = [r["fromUserId"] for r in page]
        from_user_sets = await repository.get_users_by_ids(from_user_ids)

        notifications = [
            {
                "id": r["id"],
                "notifyIcon": _generate_notify_icon(r["type"], r["fromUserId"], from_user_sets),
                "notifyType": r["type"],
                "url": r["urlPath"] or "",
                "message": r["message"],
                "messageI18n": r["messageI18n"],
                "severity": _notification_severity(r["type"], r["severity"]),
                "isRead": r["isRead"],
                "createdTime": r["createdTime"],
            }
            for r in page
        ]

        result: dict[str, Any] = {"notifications": notifications}
        if next_cursor is not None:
            # upstream returns nextCursor: undefined when exhausted -> key omitted
            result["nextCursor"] = next_cursor
        result["summary"] = {
            NotificationSeverityEnum.CRITICAL.value: summary.get(
                NotificationSeverityEnum.CRITICAL.value, 0
            ),
            NotificationSeverityEnum.WARNING.value: summary.get(
                NotificationSeverityEnum.WARNING.value, 0
            ),
            NotificationSeverityEnum.INFO.value: summary.get(
                NotificationSeverityEnum.INFO.value, 0
            ),
        }
        return result

    async def unread_count(self, user_id: str) -> dict[str, int]:
        return {"unreadCount": await repository.count_unread(user_id)}

    async def update_notify_status(
        self, user_id: str, notification_id: str, ro: UpdateNotifyStatusRo
    ) -> None:
        await repository.update_status(user_id, notification_id, ro.isRead)

    async def mark_all_as_read(self, user_id: str) -> None:
        await repository.mark_all_as_read(user_id)

    # ---- producers ---------------------------------------------------------

    async def create_and_push(
        self,
        *,
        from_user_id: str,
        to_user_id: str,
        notify_type: str,
        message: str,
        message_i18n: str | None = None,
        url_path: str = "",
        severity: str | None = None,
    ) -> str | None:
        """Create one notification row and push it over the recipient's presence
        channel. Skips self-notifications and unknown recipients (ports the
        per-recipient body of notification.service send* methods)."""
        if not to_user_id or from_user_id == to_user_id:
            return None
        recipients = await repository.get_users_by_ids([to_user_id])
        if to_user_id not in recipients:
            return None
        notify_id = new_id(IdPrefix.NOTIFICATION)
        sev = _notification_severity(notify_type, severity)
        created_time = datetime.now(UTC).replace(tzinfo=None)
        await repository.insert_notification(
            {
                "id": notify_id,
                "from_user_id": from_user_id,
                "to_user_id": to_user_id,
                "type": notify_type,
                "message": message,
                "message_i18n": message_i18n,
                "severity": sev,
                "url_path": url_path or None,
                "is_read": False,
                "created_time": created_time,
                "created_by": from_user_id,
            }
        )
        unread = await repository.count_unread(to_user_id)
        from_sets = await repository.get_users_by_ids([from_user_id])
        if notify_type in {e.value for e in _USER_ICON_TYPES} and from_user_id in from_sets:
            icon = _generate_notify_icon(notify_type, from_user_id, from_sets)
        else:
            icon = dict(_SYSTEM_ICON)
        created_iso = created_time.replace(tzinfo=UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        payload = {
            "notification": {
                "id": notify_id,
                "message": message,
                "messageI18n": message_i18n,
                "notifyIcon": icon,
                "notifyType": notify_type,
                "url": url_path or "",
                "severity": sev,
                "isRead": False,
                "createdTime": created_iso,
            },
            "unreadCount": unread,
        }
        from ...realtime.broadcast import broadcast_notification

        await broadcast_notification(to_user_id, notify_id, payload)
        return notify_id
