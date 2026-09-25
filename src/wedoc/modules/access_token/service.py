"""Access-token service — ports features/access-token/access-token.service.ts."""

import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.ids import IdPrefix, new_id, random_string
from ...core.security.auth import get_access_token
from . import repository
from .schemas import CreateAccessTokenRo, RefreshAccessTokenRo, UpdateAccessTokenRo


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_date(value: str) -> datetime:
    """new Date(value).toISOString() equivalent — normalise to naive UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:  # pragma: no cover - guarded by schema refine
            dt = datetime.now(UTC)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _transform(entity: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": entity["id"], "name": entity["name"]}
    description = entity.get("description")
    if description:
        out["description"] = description
    out["scopes"] = json.loads(entity["scopes"])
    space_ids = entity.get("spaceIds")
    if space_ids:
        out["spaceIds"] = json.loads(space_ids)
    base_ids = entity.get("baseIds")
    if base_ids:
        out["baseIds"] = json.loads(base_ids)
    if "createdTime" in entity and entity["createdTime"] is not None:
        out["createdTime"] = _iso(entity["createdTime"])
    if "lastUsedTime" in entity and entity["lastUsedTime"] is not None:
        out["lastUsedTime"] = _iso(entity["lastUsedTime"])
    if "expiredTime" in entity and entity["expiredTime"] is not None:
        out["expiredTime"] = _iso(entity["expiredTime"])
    has_full_access = entity.get("hasFullAccess")
    if has_full_access is not None:
        out["hasFullAccess"] = has_full_access
    return out


class AccessTokenService:
    async def list_access_token(self) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        rows = await repository.list_by_user(user_id)
        return [_transform(r) for r in rows]

    async def create_access_token(self, body: CreateAccessTokenRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        token_id = new_id(IdPrefix.ACCESS_TOKEN)
        sign = random_string(16)
        entity = await repository.create(
            token_id=token_id,
            name=body.name,
            description=body.description,
            scopes=json.dumps(body.scopes),
            space_ids=None if body.spaceIds is None else json.dumps(body.spaceIds),
            base_ids=None if body.baseIds is None else json.dumps(body.baseIds),
            user_id=user_id,
            sign=sign,
            client_id=None,
            expired_time=_parse_date(body.expiredTime),
            has_full_access=body.hasFullAccess,
        )
        return {**_transform(entity), "token": get_access_token(token_id, sign)}

    async def get_access_token(self, token_id: str) -> dict[str, Any]:
        user_id = cls.get("user.id")
        entity = await repository.get_or_throw(user_id, token_id)
        res = _transform(entity)
        space_ids = res.get("spaceIds")
        base_ids = res.get("baseIds")
        if space_ids is not None:
            res["spaceIds"] = await repository.filter_existing_space_ids(space_ids)
        if base_ids is not None:
            res["baseIds"] = await repository.filter_existing_base_ids(base_ids)
        return res

    async def update_access_token(
        self, token_id: str, body: UpdateAccessTokenRo
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        entity = await repository.update(
            user_id,
            token_id,
            name=body.name,
            description=body.description,
            scopes=json.dumps(body.scopes),
            space_ids=None if body.spaceIds is None else json.dumps(body.spaceIds),
            base_ids=None if body.baseIds is None else json.dumps(body.baseIds),
            has_full_access=body.hasFullAccess,
        )
        return _transform(entity)

    async def refresh_access_token(
        self, token_id: str, body: RefreshAccessTokenRo | None
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        sign = random_string(16)
        expired = _parse_date(body.expiredTime) if body and body.expiredTime else None
        entity = await repository.refresh(user_id, token_id, sign, expired)
        return {**_transform(entity), "token": get_access_token(token_id, sign)}

    async def delete_access_token(self, token_id: str) -> None:
        user_id = cls.get("user.id")
        await repository.delete(user_id, token_id)
