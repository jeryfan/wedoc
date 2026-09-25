"""Plugin developer-center + auth service — ports plugin.service.ts + plugin-auth.service.ts."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt as pyjwt

from ...compat import upstream_brand
from ...core import cls
from ...core.cache import get_cache
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id, random_string
from ...core.security.auth import JwtService, get_access_token
from ...core.storage import get_public_full_storage_url
from ..access_token import repository as access_token_repository
from ..user.service import UserService
from . import repository
from .schemas import (
    PLUGIN_BASE_ACTIONS,
    CreatePluginRo,
    PluginGetTokenRo,
    PluginRefreshTokenRo,
    UpdatePluginRo,
)

_SYSTEM = "system"
_ACCESS_TOKEN_EXPIRE = 600  # 10m
_REFRESH_TOKEN_EXPIRE = 2592000  # 30d
_AUTHORIZATION_VERSION = 1
_PLUGIN_BASE_ACTION_SET = frozenset(PLUGIN_BASE_ACTIONS)


def _plugin_id() -> str:
    return str(IdPrefix.PLUGIN) + random_string(16)


def _plugin_user_id() -> str:
    return str(IdPrefix.PLUGIN_USER) + random_string(16)


def _plugin_email(plugin_id: str) -> str:
    return f"{plugin_id.lower()}@plugin.{upstream_brand()}.ai"


def _support_email() -> str:
    return f"support@{upstream_brand()}.ai"


def _system_brand() -> str:
    return upstream_brand().capitalize()


async def generate_secret() -> tuple[str, str, str]:
    secret = random_string(40).lower()
    hashed = bcrypt.hashpw(secret.encode()[:72], bcrypt.gensalt(10)).decode()
    sensitive = len(secret) - 10
    masked = "*" * sensitive + secret[sensitive:]
    return secret, hashed, masked


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PluginService:
    def _base_vo(self, row: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": row["id"],
            "name": row["name"],
            "logo": get_public_full_storage_url(row["logo"]),
            "status": row["status"],
            "positions": json.loads(row["positions"]),
        }
        for key in ("description", "detailDesc", "helpUrl", "url"):
            if row.get(key) is not None:
                out[key] = row[key]
        if row.get("i18n"):
            out["i18n"] = json.loads(row["i18n"])
        if row.get("config"):
            out["config"] = json.loads(row["config"])
        if row.get("createdTime") is not None:
            out["createdTime"] = _iso(row["createdTime"])
        if row.get("lastModifiedTime") is not None:
            out["lastModifiedTime"] = _iso(row["lastModifiedTime"])
        return out

    def _user_vo(self, user: dict[str, Any] | None) -> dict[str, Any] | None:
        if user is None:
            return None
        out = {"id": user["id"], "name": user["name"], "email": user["email"]}
        if user.get("avatar"):
            out["avatar"] = get_public_full_storage_url(user["avatar"])
        return out

    def _system_user_vo(self) -> dict[str, Any]:
        return {"id": _SYSTEM, "name": _system_brand(), "email": _support_email()}

    async def create_plugin(self, ro: CreatePluginRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        secret, hashed, masked = await generate_secret()
        plugin_id = _plugin_id()
        plugin_user = None
        if ro.autoCreateMember:
            created = await UserService().create_system_user(
                _plugin_user_id(), _plugin_email(plugin_id), ro.name
            )
            plugin_user = created
        row = await repository.create_plugin(
            {
                "id": plugin_id,
                "name": ro.name,
                "description": ro.description,
                "detail_desc": ro.detailDesc,
                "positions": json.dumps(ro.positions),
                "help_url": ro.helpUrl,
                "url": ro.url,
                "logo": ro.logo,
                "config": json.dumps(ro.config) if ro.config is not None else None,
                "status": "developing",
                "i18n": json.dumps(ro.i18n) if ro.i18n is not None else None,
                "secret": hashed,
                "masked_secret": masked,
                "plugin_user": plugin_user["id"] if plugin_user else None,
                "created_by": user_id,
            }
        )
        vo = self._base_vo(row)
        # create select mirrors ref: no lastModifiedTime on the create response
        vo.pop("lastModifiedTime", None)
        vo["secret"] = secret
        if plugin_user:
            vo["pluginUser"] = self._user_vo(plugin_user)
        return vo

    async def get_plugin(self, plugin_id: str) -> dict[str, Any]:
        row = await repository.get_plugin(
            plugin_id, cls.get("user.id"), bool(cls.get("user.isAdmin"))
        )
        return await self._detail_vo(row, include_secret=True, include_is_system=True)

    async def _detail_vo(
        self, row: dict[str, Any], *, include_secret: bool, include_is_system: bool
    ) -> dict[str, Any]:
        vo = self._base_vo(row)
        if include_secret:
            vo["secret"] = row["maskedSecret"]
        if include_is_system:
            vo["isSystem"] = row["createdBy"] == _SYSTEM
        if row.get("pluginUser"):
            users = await repository.get_users([row["pluginUser"]])
            vo["pluginUser"] = self._user_vo(users.get(row["pluginUser"]))
        return vo

    async def update_plugin(self, plugin_id: str, ro: UpdatePluginRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        is_admin = bool(cls.get("user.isAdmin"))
        logo = ro.logo
        if logo and logo.startswith("http"):
            logo = f"/plugin/{logo.split('/')[-1]}"
        values = {
            "name": ro.name,
            "description": ro.description,
            "detail_desc": ro.detailDesc,
            "positions": json.dumps(ro.positions),
            "help_url": ro.helpUrl,
            "url": ro.url,
            "logo": logo,
            "config": json.dumps(ro.config) if ro.config is not None else None,
            "i18n": json.dumps(ro.i18n) if ro.i18n is not None else None,
            "last_modified_by": user_id,
        }
        row = await repository.update_plugin(plugin_id, user_id, is_admin, values)
        if ro.name and row.get("pluginUser"):
            await UserService().update_user_name(row["pluginUser"], ro.name)
            row["name"] = ro.name
        return await self._detail_vo(row, include_secret=True, include_is_system=False)

    async def get_plugins(self) -> list[dict[str, Any]]:
        rows = await repository.list_plugins(
            cls.get("user.id"), bool(cls.get("user.isAdmin"))
        )
        user_ids = [r["pluginUser"] for r in rows if r["pluginUser"]]
        users = await repository.get_users(user_ids)
        out = []
        for row in rows:
            vo = self._base_vo(row)
            vo["isSystem"] = row["createdBy"] == _SYSTEM
            if row["pluginUser"]:
                u = users.get(row["pluginUser"])
                vo["pluginUser"] = self._user_vo(u) if u else self._system_user_vo()
            out.append(vo)
        return out

    async def delete_plugin(self, plugin_id: str) -> None:
        await repository.delete_plugin(plugin_id, cls.get("user.id"))

    async def regenerate_secret(self, plugin_id: str) -> dict[str, Any]:
        secret, hashed, masked = await generate_secret()
        await repository.regenerate_secret(plugin_id, cls.get("user.id"), hashed, masked)
        return {"secret": secret, "id": plugin_id}

    async def submit_plugin(self, plugin_id: str) -> None:
        await repository.submit_plugin(plugin_id, cls.get("user.id"))

    async def unpublish_plugin(self, plugin_id: str) -> None:
        await repository.unpublish_plugin(plugin_id, cls.get("user.id"))

    async def get_plugin_center_list(
        self, positions: list[str] | None, ids: list[str] | None
    ) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        rows = await repository.center_list(user_id, positions, ids)
        users = await repository.get_users([r["createdBy"] for r in rows])
        out = []
        for r in rows:
            item: dict[str, Any] = {
                "id": r["id"],
                "name": r["name"],
                "logo": get_public_full_storage_url(r["logo"]),
                "status": r["status"],
            }
            for key in ("description", "detailDesc", "url", "helpUrl"):
                if r.get(key) is not None:
                    item[key] = r[key]
            if r.get("i18n"):
                item["i18n"] = json.loads(r["i18n"])
            if r.get("createdTime") is not None:
                item["createdTime"] = _iso(r["createdTime"])
            if r.get("lastModifiedTime") is not None:
                item["lastModifiedTime"] = _iso(r["lastModifiedTime"])
            user = users.get(r["createdBy"])
            item["createdBy"] = self._user_vo(user) if user else self._system_user_vo()
            out.append(item)
        return out


class PluginAuthService:
    async def _validate_secret(self, plugin_id: str, secret: str) -> dict[str, Any]:
        plugin = await repository.validate_secret_row(plugin_id, cls.get("user.id"))
        if plugin is None:
            raise ApiError(
                "Plugin not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.plugin.notFound"}},
            )
        if not plugin["pluginUser"]:
            raise ApiError(
                "Plugin user not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.plugin.userNotFound"}},
            )
        if not bcrypt.checkpw(secret.encode()[:72], plugin["secret"].encode()):
            raise ApiError(
                "Invalid secret",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.plugin.invalidSecret"}},
            )
        return plugin

    async def _assert_installed(self, plugin_id: str, base_id: str) -> None:
        installs = await repository.active_installs(plugin_id, base_id)
        if not installs:
            raise ApiError(
                "Plugin not installed",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.pluginInstall.notFound"}},
            )
        # active-parent verification (dashboard/view/panel/context-menu) is
        # simplified to install-existence here; refined checks land with panels.

    def _invalid_refresh_token(self) -> ApiError:
        return ApiError(
            "Invalid refresh token",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.plugin.invalidRefreshToken"}},
        )

    def _parse_refresh_scopes(self, scopes: str | None) -> list[str]:
        try:
            parsed = json.loads(scopes) if scopes else []
        except (ValueError, TypeError) as error:
            raise self._invalid_refresh_token() from error
        if (
            not isinstance(parsed, list)
            or len(parsed) < 1
            or any(scope not in _PLUGIN_BASE_ACTION_SET for scope in parsed)
        ):
            raise self._invalid_refresh_token()
        return parsed

    async def _generate_access_token(
        self, *, user_id: str, scopes: list[str], client_id: str, name: str, base_id: str
    ) -> dict[str, str]:
        token_id = new_id(IdPrefix.ACCESS_TOKEN)
        sign = random_string(16)
        expired = datetime.now(UTC) + timedelta(seconds=_ACCESS_TOKEN_EXPIRE)
        await access_token_repository.create(
            token_id=token_id,
            name=f"plugin:{name}",
            description=None,
            scopes=json.dumps(scopes),
            space_ids=None,
            base_ids=json.dumps([base_id]),
            user_id=user_id,
            sign=sign,
            client_id=client_id,
            expired_time=expired.replace(tzinfo=None),
            has_full_access=None,
        )
        return {"id": token_id, "token": get_access_token(token_id, sign)}

    def _generate_refresh_token(
        self, *, plugin_id: str, secret: str, access_token_id: str
    ) -> str:
        return JwtService().sign(
            {
                "secret": secret,
                "accessTokenId": access_token_id,
                "pluginId": plugin_id,
                "authorizationVersion": _AUTHORIZATION_VERSION,
            },
            expires_in=_REFRESH_TOKEN_EXPIRE,
        )

    async def token(self, plugin_id: str, ro: PluginGetTokenRo) -> dict[str, Any]:
        plugin = await self._validate_secret(plugin_id, ro.secret)
        cache = get_cache()
        key = f"plugin:auth-code:{ro.authCode}"
        state = await cache.get(key)
        if not state:
            raise ApiError("Invalid auth code", HttpErrorCode.VALIDATION_ERROR)
        await cache.delete(key)
        if state.get("pluginId") != plugin_id or state.get("baseId") != ro.baseId:
            raise ApiError("Invalid auth code", HttpErrorCode.VALIDATION_ERROR)
        await self._assert_installed(plugin_id, ro.baseId)
        access_token = await self._generate_access_token(
            user_id=plugin["pluginUser"],
            scopes=ro.scopes,
            client_id=plugin_id,
            name=plugin["name"],
            base_id=ro.baseId,
        )
        refresh_token = self._generate_refresh_token(
            plugin_id=plugin_id, secret=ro.secret, access_token_id=access_token["id"]
        )
        return {
            "accessToken": access_token["token"],
            "refreshToken": refresh_token,
            "scopes": ro.scopes,
            "expiresIn": _ACCESS_TOKEN_EXPIRE,
            "refreshExpiresIn": _REFRESH_TOKEN_EXPIRE,
        }

    async def refresh_token(self, plugin_id: str, ro: PluginRefreshTokenRo) -> dict[str, Any]:
        plugin = await self._validate_secret(plugin_id, ro.secret)
        try:
            payload = JwtService().verify(ro.refreshToken)
        except pyjwt.PyJWTError as error:
            raise self._invalid_refresh_token() from error
        if (
            payload.get("pluginId") != plugin_id
            or payload.get("secret") != ro.secret
            or payload.get("accessTokenId") is None
            or payload.get("authorizationVersion") != _AUTHORIZATION_VERSION
        ):
            raise self._invalid_refresh_token()
        access_token_id = payload["accessTokenId"]
        source = await repository.refresh_token_source(access_token_id)
        if source is None:
            raise self._invalid_refresh_token()
        base_ids = json.loads(source["baseIds"]) if source["baseIds"] else []
        base_id = base_ids[0] if base_ids else ""
        scopes = self._parse_refresh_scopes(source["scopes"])
        if not base_id:
            raise ApiError(
                "Anomalous token with no baseId",
                HttpErrorCode.INTERNAL_SERVER_ERROR,
                {"localization": {"i18nKey": "httpErrors.plugin.anomalousToken"}},
            )
        await self._assert_installed(plugin_id, base_id)
        await access_token_repository.delete(plugin["pluginUser"], access_token_id)
        access_token = await self._generate_access_token(
            user_id=plugin["pluginUser"],
            scopes=scopes,
            client_id=plugin_id,
            name=plugin["name"],
            base_id=base_id,
        )
        refresh_token = self._generate_refresh_token(
            plugin_id=plugin_id, secret=ro.secret, access_token_id=access_token["id"]
        )
        return {
            "accessToken": access_token["token"],
            "refreshToken": refresh_token,
            "scopes": scopes,
            "expiresIn": _ACCESS_TOKEN_EXPIRE,
            "refreshExpiresIn": _REFRESH_TOKEN_EXPIRE,
        }

    async def auth_code(self, plugin_id: str, base_id: str) -> str:
        await self._assert_installed(plugin_id, base_id)
        auth_code = random_string(16)
        await get_cache().set(
            f"plugin:auth-code:{auth_code}", {"baseId": base_id, "pluginId": plugin_id}, 300
        )
        return auth_code
