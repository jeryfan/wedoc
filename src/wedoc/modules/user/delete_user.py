"""DeleteUserService port: guarded account teardown behind DELETE /api/auth/user."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, update

from ...compat import deleted_user_email
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import random_string
from ...core.storage import get_storage
from ...db import engine as db_engine
from ...db.models_meta import (
    AccessToken,
    Account,
    Collaborator,
    CommentSubscription,
    Invitation,
    Notification,
    OAuthApp,
    PinResource,
    Plugin,
    Space,
    User,
)
from . import repository

DELETED_USER_AVATAR = (
    Path(__file__).resolve().parents[2] / "core" / "assets" / "deleted-user-avatar.png"
)


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


class DeleteUserService:
    async def _validate_delete_user(self, user_id: str) -> None:
        async with db_engine.session() as session:
            rows = (
                await session.execute(
                    select(Space.id, Space.name, Space.deleted_time)
                    .select_from(Collaborator)
                    .join(Space, Collaborator.resource_id == Space.id)
                    .where(
                        Collaborator.principal_id == user_id,
                        Collaborator.principal_type == "user",
                    )
                    .where(
                        (
                            Collaborator.role_name.in_(["owner", "creator"])
                            & Space.deleted_time.isnot(None)
                        )
                        | Space.deleted_time.is_(None)
                    )
                )
            ).all()
        if rows:
            names = ", ".join(row[1] for row in rows)
            raise ApiError(
                f"User has collaborators in spaces (or deleted spaces in trash): {names}",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "spaces": [
                        {
                            "id": row[0],
                            "name": row[1],
                            "deletedTime": (
                                row[2].isoformat(timespec="milliseconds").replace("+00:00", "Z")
                                if row[2]
                                else None
                            ),
                        }
                        for row in rows
                    ],
                    "localization": {"i18nKey": "httpErrors.user.collaboratorsInSpaces"},
                },
            )

    async def _clear_user_data(self, user_id: str) -> None:
        from ...db.models_meta import OAuthAppAuthorized, OAuthAppSecret, OAuthAppToken

        async with db_engine.session() as session:
            await session.execute(delete(AccessToken).where(AccessToken.user_id == user_id))
            await session.execute(delete(Account).where(Account.user_id == user_id))
            await session.execute(
                delete(CommentSubscription).where(CommentSubscription.created_by == user_id)
            )
            await session.execute(delete(Invitation).where(Invitation.create_by == user_id))
            await session.execute(delete(Notification).where(Notification.to_user_id == user_id))
            app_client_ids = select(OAuthApp.client_id).where(OAuthApp.created_by == user_id)
            secret_ids = select(OAuthAppSecret.id).where(
                OAuthAppSecret.client_id.in_(app_client_ids)
            )
            await session.execute(
                delete(OAuthAppToken).where(OAuthAppToken.app_secret_id.in_(secret_ids))
            )
            await session.execute(
                delete(OAuthAppSecret).where(OAuthAppSecret.client_id.in_(app_client_ids))
            )
            await session.execute(
                delete(OAuthAppAuthorized).where(
                    OAuthAppAuthorized.client_id.in_(app_client_ids)
                )
            )
            await session.execute(delete(OAuthApp).where(OAuthApp.created_by == user_id))
            await session.execute(delete(PinResource).where(PinResource.created_by == user_id))
            await session.execute(
                delete(Plugin).where(Plugin.created_by == user_id, Plugin.status != "published")
            )
            await session.commit()
        await repository.delete_last_visits_for_user(user_id)
        async with db_engine.session() as session:
            await session.execute(
                delete(Collaborator).where(Collaborator.principal_id == user_id)
            )
            await session.commit()

    async def _update_avatar_to_deleted(self, user_id: str) -> None:
        path = f"avatar/{user_id}"
        png = DELETED_USER_AVATAR.read_bytes()
        storage = get_storage()
        from ...config import get_settings

        bucket = get_settings().backend_storage_public_bucket
        result = storage.upload_file(bucket, path, png)
        await repository.update_attachment_hash_by_token(user_id, str(result["hash"]))
        import time

        async with db_engine.session() as session:
            await session.execute(
                # no deleted_time filter: the row is already soft-deleted here
                update(User)
                .where(User.id == user_id)
                .values(avatar=f"{path}?v={int(time.time() * 1000)}")
            )
            await session.commit()

    async def delete_user_by_id(self, user_id: str) -> None:
        await self._validate_delete_user(user_id)
        await self._clear_user_data(user_id)
        async with db_engine.session() as session:
            await session.execute(
                update(User)
                .where(User.id == user_id, User.permanent_deleted_time.is_(None))
                .values(
                    email=deleted_user_email(f"deleted-{random_string(10)}"),
                    name="Deleted User",
                    permanent_deleted_time=_now(),
                    deleted_time=_now(),
                )
            )
            await session.commit()
        await self._update_avatar_to_deleted(user_id)

    async def delete_user(self) -> None:
        from ...core import cls

        await self.delete_user_by_id(cls.get("user.id"))
