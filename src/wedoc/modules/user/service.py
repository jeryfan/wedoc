"""UserService port: user CRUD used by both the user and auth modules."""

import json
import time
from typing import Any

from ...config import Settings, get_settings
from ...core import cls
from ...core.avatar import crop_square_avatar, render_default_avatar
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...core.storage import get_public_full_storage_url, get_storage
from ..setting import repository as setting_repository
from . import repository

AVATAR_BUCKET_DIR = "avatar"


def is_email_domain_banned(email: str, banned_domains: list[str] | None) -> bool:
    if not banned_domains:
        return False
    domain = email.split("@")[1].strip().lower() if "@" in email else ""
    if not domain:
        return False
    for banned in banned_domains:
        normalized = banned.strip().lstrip("@").lower()
        if normalized and (domain == normalized or domain.endswith(f".{normalized}")):
            return True
    return False


def with_storage_urls(user: dict[str, Any]) -> dict[str, Any]:
    """avatar path -> public url, notify_meta JSON parse (UserService.getUserById)."""
    result = dict(user)
    avatar = result.get("avatar")
    if avatar and not avatar.startswith("http"):
        result["avatar"] = get_public_full_storage_url(avatar)
    notify_meta = result.get("notify_meta")
    if isinstance(notify_meta, str):
        result["notify_meta"] = json.loads(notify_meta)
    return result


class UserService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        row = await repository.get_user_row_by_id(user_id)
        return with_storage_urls(row) if row else None

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return await repository.get_user_row_by_email(email)

    async def get_setting(self) -> dict[str, Any]:
        return await setting_repository.get_setting()

    def throw_if_email_domain_banned(
        self, email: str, banned_email_domains: list[str] | None
    ) -> None:
        if is_email_domain_banned(email, banned_email_domains):
            raise ApiError(
                "This email domain has been banned due to policy violations",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.user.emailDomainBanned"}},
            )

    async def check_waitlist_invite_code(self, invite_code: str | None) -> None:
        from ...core.cache import get_cache

        if not invite_code:
            raise ApiError(
                "Waitlist is enabled, invite code is required",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.user.waitlistInviteCodeRequired"}},
            )
        cache = get_cache()
        times = await cache.get(f"waitlist:invite-code:{invite_code}")
        if not times or times <= 0:
            raise ApiError(
                "Waitlist is enabled, invite code is invalid",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.user.waitlistInviteCodeInvalid"}},
            )
        await cache.set(f"waitlist:invite-code:{invite_code}", times - 1, "30d")

    async def create_user_with_setting_check(
        self,
        user: dict[str, Any],
        account: dict[str, str] | None = None,
        default_space_name: str | None = None,
        invite_code: str | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        setting = await self.get_setting()
        if setting.get("disallowSignUp"):
            raise ApiError(
                "The current instance disallow sign up by the administrator",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.user.disallowSignUp"}},
            )
        self.throw_if_email_domain_banned(user["email"], setting.get("bannedEmailDomains"))
        if setting.get("enableWaitlist"):
            await self.check_waitlist_invite_code(invite_code)
        return await self.create_user(user, account, default_space_name, lang=lang)

    async def create_user(
        self,
        user: dict[str, Any],
        account: dict[str, str] | None = None,
        default_space_name: str | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        user_id = user.get("id") or new_id(IdPrefix.USER)
        email = user["email"].lower()
        has_admin_user = await repository.has_non_system_user()
        avatar = user.get("avatar") or await self._generate_default_avatar(user_id)
        row = await repository.create_user_row(
            {
                **user,
                "id": user_id,
                "email": email,
                "name": user.get("name") or email.split("@")[0],
                "notify_meta": json.dumps({"email": True}, separators=(",", ":")),
                "avatar": avatar,
                "is_admin": None if has_admin_user else True,
                "lang": lang,
            }
        )
        if account:
            await repository.create_account_row(
                user_id, account["provider"], account["provider_id"], account["type"]
            )
        # auto-space creation is cloud-edition only (baseConfig.isCloud) — the
        # self-hosted default never creates a space here
        return row

    async def _generate_default_avatar(self, user_id: str) -> str:
        path = f"{AVATAR_BUCKET_DIR}/{user_id}"
        png = render_default_avatar(user_id)
        storage = get_storage()
        bucket = self._settings.backend_storage_public_bucket
        result = storage.upload_file(bucket, path, png)
        await repository.upsert_attachment_by_token(
            {
                "token": user_id,
                "hash": result["hash"],
                "size": len(png),
                "mimetype": "image/png",
                "path": path,
                "width": 410,
                "height": 410,
                "created_by": user_id,
            }
        )
        return path

    async def update_user_name(self, user_id: str, name: str) -> None:
        await repository.update_user_row(user_id, {"name": name})

    async def create_system_user(
        self, user_id: str, email: str, name: str
    ) -> dict[str, Any]:
        """Port of UserService.createSystemUser: is_system row + default avatar."""
        avatar = await self._generate_default_avatar(user_id)
        return await repository.create_user_row(
            {
                "id": user_id,
                "email": email.lower(),
                "name": name,
                "avatar": avatar,
                "is_system": True,
            }
        )

    async def update_avatar(self, user_id: str, file_bytes: bytes) -> None:
        cropped = crop_square_avatar(file_bytes)
        path = f"{AVATAR_BUCKET_DIR}/{user_id}"
        storage = get_storage()
        bucket = self._settings.backend_storage_public_bucket
        result = storage.upload_file(bucket, path, cropped)
        from ...core.avatar import AVATAR_OUTPUT_MIMETYPE

        await repository.upsert_attachment_by_token(
            {
                "token": user_id,
                "hash": result["hash"],
                "size": len(cropped),
                "mimetype": AVATAR_OUTPUT_MIMETYPE,
                "path": path,
                "created_by": user_id,
            }
        )
        version_ms = int(time.time() * 1000)
        await repository.update_user_row(user_id, {"avatar": f"{path}?v={version_ms}"})

    async def update_notify_meta(self, user_id: str, notify_meta: dict[str, Any]) -> None:
        await repository.update_notify_meta(user_id, notify_meta)

    async def update_lang(self, user_id: str, lang: str) -> None:
        await repository.update_user_row(user_id, {"lang": lang})

    async def refresh_last_sign_time(self, user_id: str) -> None:
        from datetime import UTC, datetime

        await repository.update_user_row(
            user_id, {"last_sign_time": datetime.now(tz=UTC).replace(tzinfo=None)}
        )

    def current_user_id(self) -> str:
        return cls.get("user.id")
