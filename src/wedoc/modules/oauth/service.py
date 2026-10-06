"""OAuth client service — ports features/oauth/oauth.service.ts (client management)."""

import json
from typing import Any

import bcrypt

from ...compat import upstream_brand
from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, cuid, random_string
from . import repository
from .schemas import OAuthCreateRo

SYSTEM_USER_ID = "system"


def _system_email() -> str:
    return f"system@{upstream_brand()}.ai"


def _client_id() -> str:
    return str(IdPrefix.OAUTH_CLIENT) + random_string(16).lower()


def _convert_to_vo(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key == "scopes":
            if value:
                out["scopes"] = json.loads(value)
            continue
        if key == "redirectUris":
            if value:
                out["redirectUris"] = json.loads(value)
            continue
        if value is not None:
            out[key] = value
    return out


class OAuthService:
    async def _validate_ownership(self, client_id: str) -> None:
        created_by = await repository.get_created_by(client_id)
        if created_by is None:
            raise ApiError("OAuth client not found", HttpErrorCode.NOT_FOUND)
        if cls.get("user.isAdmin") and created_by == SYSTEM_USER_ID:
            return
        if created_by != cls.get("user.id"):
            raise ApiError(
                "No permission to operate on this OAuth client", HttpErrorCode.RESTRICTED_RESOURCE
            )

    async def create_oauth(self, ro: OAuthCreateRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        values: dict[str, Any] = {
            "id": cuid(),
            "name": ro.name,
            "description": ro.description,
            "scopes": json.dumps(ro.scopes) if ro.scopes else None,
            "homepage": ro.homepage,
            "logo": ro.logo,
            "redirect_uris": json.dumps(ro.redirectUris) if ro.redirectUris else None,
            "created_by": user_id,
            "client_id": _client_id(),
        }
        if ro.allowDeviceFlow is not None:
            values["allow_device_flow"] = ro.allowDeviceFlow
        row = await repository.create_app(values)
        return _convert_to_vo(
            {k: row[k] for k in (
                "id", "name", "description", "scopes", "homepage", "logo",
                "redirectUris", "allowDeviceFlow", "clientId",
            )}
        )

    async def get_oauth(self, client_id: str) -> dict[str, Any]:
        await self._validate_ownership(client_id)
        row = await repository.get_app(client_id)
        if row is None:
            raise ApiError("OAuth client not found", HttpErrorCode.NOT_FOUND)
        secrets = await repository.list_secrets(client_id)
        base = {k: row[k] for k in (
            "id", "name", "description", "scopes", "homepage", "logo",
            "redirectUris", "allowDeviceFlow", "clientId",
        )}
        if secrets:
            base["secrets"] = [_convert_to_vo(s) for s in secrets]
        return _convert_to_vo(base)

    async def update_oauth(self, client_id: str, ro: OAuthCreateRo) -> dict[str, Any]:
        await self._validate_ownership(client_id)
        # An omitted optional field must preserve the stored value (Prisma skips
        # `undefined`); only columns present in the request body are written.
        provided = ro.model_fields_set
        values: dict[str, Any] = {
            "name": ro.name,
            "homepage": ro.homepage,
            "redirect_uris": json.dumps(ro.redirectUris) if ro.redirectUris else None,
        }
        if "description" in provided:
            values["description"] = ro.description
        if "logo" in provided:
            values["logo"] = ro.logo
        if "scopes" in provided:
            values["scopes"] = json.dumps(ro.scopes) if ro.scopes else None
        if "allowDeviceFlow" in provided:
            values["allow_device_flow"] = ro.allowDeviceFlow
        row = await repository.update_app(client_id, values)
        return _convert_to_vo(
            {k: row[k] for k in (
                "id", "name", "description", "scopes", "homepage", "logo",
                "redirectUris", "allowDeviceFlow", "clientId",
            )}
        )

    async def delete_oauth(self, client_id: str) -> None:
        await self._validate_ownership(client_id)
        await repository.delete_app(client_id)

    async def get_oauth_list(self) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        creators = [user_id]
        if cls.get("user.isAdmin"):
            creators = [user_id, SYSTEM_USER_ID]
        rows = await repository.list_apps({"created_by": creators})
        return [_convert_to_vo(row) for row in rows]

    async def generate_secret(self, client_id: str) -> dict[str, Any]:
        await self._validate_ownership(client_id)
        secret = random_string(40).lower()
        hashed = bcrypt.hashpw(secret.encode()[:72], bcrypt.gensalt(10)).decode()
        sensitive_len = len(secret) - 10
        masked = "*" * sensitive_len + secret[sensitive_len:]
        secret_row = await repository.create_secret(
            {
                "id": cuid(),
                "client_id": client_id,
                "secret": hashed,
                "masked_secret": masked,
                "created_by": cls.get("user.id"),
            }
        )
        out = {"secret": secret, "maskedSecret": masked, "id": secret_row["id"]}
        if secret_row["lastUsedTime"] is not None:
            out["lastUsedTime"] = secret_row["lastUsedTime"]
        return out

    async def delete_secret(self, client_id: str, secret_id: str) -> None:
        await self._validate_ownership(client_id)
        deleted = await repository.delete_secret(client_id, secret_id)
        if deleted == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)

    async def revoke_access(self, client_id: str) -> None:
        await self._validate_ownership(client_id)
        await repository.revoke_access(client_id)

    async def revoke_token(self, client_id: str) -> None:
        user_id = cls.get("user.id")
        deleted = await repository.revoke_token(client_id, user_id)
        if deleted == 0:
            # prisma delete on a missing authorization row throws P2025 -> 500
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)

    async def get_authorized_list(self) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        client_ids = await repository.list_authorized(user_id)
        if not client_ids:
            return []
        apps = await repository.apps_by_client_ids(client_ids)
        if not apps:
            return []
        user_map = await repository.users_by_ids([a.created_by for a in apps])
        result = []
        for app in apps:
            creator = user_map.get(app.created_by)
            if creator is None:
                creator = (
                    {"name": "System", "email": _system_email()}
                    if app.created_by == SYSTEM_USER_ID
                    else {"name": "Unknown", "email": ""}
                )
            result.append(
                _convert_to_vo(
                    {
                        "clientId": app.client_id,
                        "name": app.name,
                        "description": app.description,
                        "logo": app.logo,
                        "homepage": app.homepage,
                        "scopes": app.scopes,
                        "createdUser": creator,
                    }
                )
            )
        return result
