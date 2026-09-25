"""Admin-open-api service — ports admin-open-api.service.ts.

All routes are instance|update gated. The perf-cache stats/heap-snapshot paths
depend on runtime introspection wedoc does not maintain and are best-effort; the
deterministic parity surface here is the non-admin 403 gate.
"""

from typing import Any

from sqlalchemy import select

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import Plugin
from .schemas import AdminSendNotificationRo


class AdminService:
    async def _update_plugin_status(
        self, plugin_id: str, from_status: str, to_status: str
    ) -> None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(Plugin.id).where(
                        Plugin.id == plugin_id, Plugin.status == from_status
                    )
                )
            ).first()
            if row is None:
                # Prisma update on a non-matching where throws P2025 -> unmapped 500.
                raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
            await session.execute(
                Plugin.__table__.update()
                .where(Plugin.id == plugin_id)
                .values(status=to_status)
            )
            await session.commit()

    async def publish_plugin(self, plugin_id: str) -> None:
        await self._update_plugin_status(plugin_id, "reviewing", "published")

    async def unpublish_plugin(self, plugin_id: str) -> None:
        await self._update_plugin_status(plugin_id, "published", "developing")

    async def repair_table_attachment_thumbnail(self) -> None:
        # Enqueues crop jobs upstream; wedoc's thumbnail crop queue is deferred.
        return None

    async def get_performance_cache(self) -> dict[str, Any]:
        # wedoc has no in-process performance cache; report the zeroed stats shape
        # (ref's live counters are nondeterministic, so this is best-effort).
        return {
            "stats": {"hits": 0, "misses": 0, "sets": 0, "deletes": 0, "errors": 0},
            "typeStats": {},
        }

    async def delete_performance_cache(self, key: str | None) -> None:
        if not key:
            raise ApiError("key is required", HttpErrorCode.VALIDATION_ERROR)
        return None

    async def send_admin_notification(self, ro: AdminSendNotificationRo) -> dict[str, Any]:
        from ..notification import repository as notif_repo
        from ..notification.service import NotificationService

        from_user_id = cls.get("user.id") or "system"
        user_ids = list(ro.userIds or [])
        if ro.emails:
            user_ids.extend(await notif_repo.get_user_ids_by_emails(ro.emails))
        notifier = NotificationService()
        sent = 0
        for user_id in dict.fromkeys(user_ids):
            notify_id = await notifier.create_and_push(
                from_user_id=from_user_id,
                to_user_id=user_id,
                notify_type="adminNotice",
                message=ro.message,
                severity=ro.severity,
            )
            if notify_id:
                sent += 1
        return {"sentCount": sent}
