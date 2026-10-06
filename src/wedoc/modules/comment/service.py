"""Comment domain service — ports comment-open-api.service.ts.

REST read/write paths. Socket presence patches (getCommentChannel /
getTableCommentChannel) are fire-and-forget upstream and do not shape any REST
response; they are omitted here (documented gap, like the M3 realtime notes).
"""

import json
import math
import time
from datetime import UTC, datetime
from typing import Any

from ...config import get_settings
from ...core import cls
from ...core.duration import parse_ms
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...core.storage import READ_PATH, get_public_full_storage_url, storage_token_encryptor
from ..record.service import RecordService
from . import repository
from .schemas import CommentNodeType

_PRIVATE_BUCKET_ATTR = "backend_storage_private_bucket"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _now_naive() -> datetime:
    # TIMESTAMP WITHOUT TIME ZONE columns reject tz-aware datetimes; store UTC.
    return datetime.now(UTC).replace(tzinfo=None)


def _comment_not_found() -> ApiError:
    return ApiError("Comment not found", HttpErrorCode.NOT_FOUND)


class CommentService:
    def _private_bucket(self) -> str:
        return getattr(get_settings(), _PRIVATE_BUCKET_ATTR, None) or "private"

    def _preview_url_by_path(self, bucket: str, path: str, _token: str) -> str:
        settings = get_settings()
        expires_in = parse_ms(settings.backend_storage_url_expire_in) // 1000
        payload = {"expiresDate": math.floor(time.time()) + expires_in}
        enc = storage_token_encryptor(settings).encrypt(payload)
        url = f"{READ_PATH}/{bucket}/{path}?token={enc}"
        origin = cls.get("origin") or {}
        prefix = settings.storage_url_prefix if origin.get("byApi") else ""
        if url and not url.startswith("/"):
            url = "/" + url
        return prefix + url

    # -- content helpers -------------------------------------------------------
    def _collections_context(self, content: list | None) -> dict[str, list[str]]:
        if not content:
            return {"imagePaths": [], "mentionUserIds": []}
        image_paths: list[str] = []
        mention_user_ids: list[str] = []
        for item in content:
            if item.get("type") == CommentNodeType.IMG.value:
                image_paths.append(item["path"])
            elif item.get("type") == CommentNodeType.PARAGRAPH.value:
                for child in item.get("children", []):
                    if child.get("type") == CommentNodeType.MENTION.value:
                        mention_user_ids.append(child["value"])
        return {"imagePaths": image_paths, "mentionUserIds": mention_user_ids}

    async def _get_presigned_url_map(self, paths: list[str]) -> dict[str, str]:
        bucket = self._private_bucket()
        result: dict[str, str] = {}
        for path in paths:
            token = path.split("/")[-1]
            result[path] = self._preview_url_by_path(bucket, path, token)
        return result

    async def _get_user_info_map(
        self, user_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        users = await repository.get_users_by_ids(list(dict.fromkeys(user_ids)))
        out: dict[str, dict[str, Any]] = {}
        for uid, u in users.items():
            info: dict[str, Any] = {"id": u["id"], "name": u["name"]}
            avatar = u.get("avatar")
            if avatar:
                info["avatar"] = get_public_full_storage_url(avatar)
            else:
                info["avatar"] = None
            out[uid] = info
        return out

    def _user_vo(self, info: dict[str, Any]) -> dict[str, Any]:
        vo: dict[str, Any] = {"id": info["id"], "name": info["name"]}
        if info.get("avatar"):
            vo["avatar"] = info["avatar"]
        return vo

    def _additional_content_context(
        self,
        content: list | None,
        image_path_map: dict[str, str],
        mention_user_map: dict[str, dict[str, Any]],
    ) -> list | None:
        if not content:
            return None
        out = []
        for item in content:
            t = item.get("type")
            if t == CommentNodeType.IMG.value:
                out.append({**item, "url": image_path_map.get(item["path"])})
            elif t == CommentNodeType.PARAGRAPH.value:
                children = []
                for child in item.get("children", []):
                    if child.get("type") == CommentNodeType.MENTION.value:
                        info = mention_user_map[child["value"]]
                        merged = {**child, "name": info["name"]}
                        # upstream sets avatar to undefined when absent -> key omitted
                        if info.get("avatar"):
                            merged["avatar"] = info["avatar"]
                        else:
                            merged.pop("avatar", None)
                        children.append(merged)
                    else:
                        children.append(child)
                out.append({**item, "children": children})
            else:
                raise ApiError(
                    f"Invalid comment content type: {t}", HttpErrorCode.VALIDATION_ERROR
                )
        return out

    def _filter_comment_content(self, content: list) -> list:
        out = []
        for item in content:
            if item.get("type") == CommentNodeType.IMG.value:
                out.append({k: v for k, v in item.items() if k != "url"})
            elif item.get("type") == CommentNodeType.PARAGRAPH.value:
                children = []
                for child in item.get("children", []):
                    if child.get("type") == CommentNodeType.MENTION.value:
                        children.append(
                            {k: v for k, v in child.items() if k not in ("name", "avatar")}
                        )
                    else:
                        children.append(child)
                out.append({**item, "children": children})
            else:
                out.append(item)
        return out

    async def _validate_quote_id(
        self, table_id: str, record_id: str, quote_id: str | None
    ) -> None:
        if not quote_id:
            return
        found = await repository.find_comment(table_id, record_id, quote_id)
        if not found:
            raise _comment_not_found()

    # -- reads -----------------------------------------------------------------
    async def get_comment_detail(
        self, table_id: str, record_id: str, comment_id: str
    ) -> dict[str, Any] | None:
        raw = await repository.find_comment(table_id, record_id, comment_id)
        if not raw:
            return None
        content = json.loads(raw["content"]) if raw["content"] else None
        reaction = json.loads(raw["reaction"]) if raw["reaction"] else []
        ctx = self._collections_context(content)
        image_path_map = await self._get_presigned_url_map(ctx["imagePaths"])
        mention_ids = list(
            {
                *ctx["mentionUserIds"],
                raw["createdBy"],
                *[u for item in reaction for u in item["user"]],
            }
        )
        mention_user_map = await self._get_user_info_map(mention_ids)
        comment_content = self._additional_content_context(
            content, image_path_map, mention_user_map
        )
        full_reaction = [
            {
                "reaction": item["reaction"],
                "user": [
                    self._user_vo(mention_user_map[uid])
                    for uid in item["user"]
                    if uid in mention_user_map
                ],
            }
            for item in reaction
        ]
        vo: dict[str, Any] = {
            "id": raw["id"],
            "createdBy": self._user_vo(mention_user_map[raw["createdBy"]]),
            "createdTime": _iso(raw["createdTime"]),
            "content": comment_content or [],
            "reaction": full_reaction if full_reaction else None,
        }
        if raw["lastModifiedTime"] is not None:
            vo["lastModifiedTime"] = _iso(raw["lastModifiedTime"])
        if raw["deletedTime"] is not None:
            vo["deletedTime"] = _iso(raw["deletedTime"])
        if raw["quoteId"]:
            vo["quoteId"] = raw["quoteId"]
        return vo

    async def get_comment_list(
        self,
        table_id: str,
        record_id: str,
        take: int | float,
        cursor: str | None,
        direction: str,
        include_cursor: bool,
    ) -> dict[str, Any]:
        if take > 1000:
            raise ApiError(
                f"take {take} exceed the max count comment list count 1000",
                HttpErrorCode.VALIDATION_ERROR,
            )
        take_with_direction = -(take + 1) if direction == "forward" else take + 1
        skip = 0 if not cursor else (0 if include_cursor else 1)
        raw_comments = await repository.list_comments(
            table_id, record_id, take_with_direction, cursor, skip
        )
        has_next_page = len(raw_comments) > take
        next_cursor = None
        if has_next_page:
            if direction == "forward":
                next_cursor = raw_comments.pop(0)["id"]
            else:
                next_cursor = raw_comments.pop()["id"]

        parsed = [
            {
                **c,
                "content": json.loads(c["content"]) if c["content"] else None,
                "reaction": json.loads(c["reaction"]) if c["reaction"] else None,
            }
            for c in raw_comments
        ]

        image_paths: set[str] = set()
        mention_user_ids: set[str] = set()
        for c in parsed:
            ctx = self._collections_context(c["content"])
            mention_user_ids.add(c["createdBy"])
            image_paths.update(ctx["imagePaths"])
            mention_user_ids.update(ctx["mentionUserIds"])
            if c["reaction"]:
                for item in c["reaction"]:
                    mention_user_ids.update(item["user"])

        image_path_map = await self._get_presigned_url_map(list(image_paths))
        mention_user_map = await self._get_user_info_map(list(mention_user_ids))

        comments = []
        for c in parsed:
            full_content = (
                self._additional_content_context(c["content"], image_path_map, mention_user_map)
                or []
            )
            vo: dict[str, Any] = {
                "id": c["id"],
                "createdBy": self._user_vo(mention_user_map[c["createdBy"]]),
                "content": full_content,
                "createdTime": _iso(c["createdTime"]),
            }
            if c["reaction"] is not None:
                vo["reaction"] = [
                    {
                        "reaction": item["reaction"],
                        "user": [
                            self._user_vo(mention_user_map[uid])
                            for uid in item["user"]
                            if uid in mention_user_map
                        ],
                    }
                    for item in c["reaction"]
                ]
            if c["quoteId"]:
                vo["quoteId"] = c["quoteId"]
            if c["lastModifiedTime"] is not None:
                vo["lastModifiedTime"] = _iso(c["lastModifiedTime"])
            comments.append(vo)

        result: dict[str, Any] = {"comments": comments}
        result["nextCursor"] = next_cursor
        return result

    async def get_record_comment_count(
        self, table_id: str, record_id: str
    ) -> dict[str, int]:
        return {"count": await repository.count_record_comments(table_id, record_id)}

    async def get_table_comment_count(
        self, table_id: str, query: dict[str, Any]
    ) -> list[dict[str, Any]]:
        ignore_view_query = bool(query.get("ignoreViewQuery"))
        listing = await RecordService().list_records(
            table_id,
            field_key_type="id",
            view_id=None if ignore_view_query else query.get("viewId"),
            filter_param=query.get("filter"),
            order_by=query.get("orderBy"),
            group_by=query.get("groupBy"),
            collapsed_group_ids=query.get("collapsedGroupIds"),
            search=query.get("search"),
            ignore_view_query=ignore_view_query,
            take=query.get("take") or 100,
            skip=query.get("skip") or 0,
        )
        ids = [r["id"] for r in listing["records"]]
        return await repository.group_counts_by_records(table_id, ids)

    # -- writes ----------------------------------------------------------------
    async def create_comment(
        self, table_id: str, record_id: str, quote_id: str | None, content: list
    ) -> dict[str, Any]:
        await self._validate_quote_id(table_id, record_id, quote_id)
        comment_id = new_id(IdPrefix.COMMENT)
        filtered = self._filter_comment_content(content)
        row = await repository.create_comment(
            comment_id,
            table_id,
            record_id,
            json.dumps(filtered, ensure_ascii=False, separators=(",", ":")),
            cls.get("user.id"),
            quote_id,
        )
        await self._send_comment_notify(
            table_id, record_id, comment_id, row["quoteId"], row["content"]
        )
        return {
            "id": row["id"],
            "tableId": row["tableId"],
            "recordId": row["recordId"],
            "quoteId": row["quoteId"],
            "content": json.loads(row["content"]) if row["content"] else None,
            "reaction": row["reaction"],
            "deletedTime": _iso(row["deletedTime"]),
            "createdTime": _iso(row["createdTime"]),
            "createdBy": row["createdBy"],
            "lastModifiedTime": _iso(row["lastModifiedTime"]),
        }

    async def update_comment(
        self, table_id: str, record_id: str, comment_id: str, content: list
    ) -> None:
        count = await repository.update_comment_content(
            table_id,
            record_id,
            comment_id,
            cls.get("user.id"),
            json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            _now_naive(),
        )
        if not count:
            raise _comment_not_found()
        row = await repository.find_comment(table_id, record_id, comment_id)
        if not row:
            raise _comment_not_found()
        await self._send_comment_notify(
            table_id, record_id, comment_id, row["quoteId"], row["content"]
        )

    async def delete_comment(
        self, table_id: str, record_id: str, comment_id: str
    ) -> None:
        count = await repository.soft_delete_comment(
            table_id, record_id, comment_id, cls.get("user.id"), _now_naive()
        )
        if not count:
            raise _comment_not_found()

    async def create_comment_reaction(
        self, table_id: str, record_id: str, comment_id: str, reaction: str
    ) -> None:
        raw = await repository.find_comment(table_id, record_id, comment_id)
        if not raw:
            raise _comment_not_found()
        user_id = cls.get("user.id")
        if raw["reaction"]:
            emojis = json.loads(raw["reaction"])
            index = next(
                (i for i, item in enumerate(emojis) if item["reaction"] == reaction), -1
            )
            if index > -1:
                users = list(dict.fromkeys([*emojis[index]["user"], user_id]))
                emojis[index] = {"reaction": reaction, "user": users}
            else:
                emojis.append({"reaction": reaction, "user": [user_id]})
            data = emojis
        else:
            data = [{"reaction": reaction, "user": [user_id]}]
        count = await repository.update_reaction(
            table_id,
            record_id,
            comment_id,
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            raw["lastModifiedTime"],
        )
        if not count:
            raise _comment_not_found()
        await self._send_comment_notify(
            table_id, record_id, comment_id, raw["quoteId"], raw["content"]
        )

    async def delete_comment_reaction(
        self, table_id: str, record_id: str, comment_id: str, reaction: str
    ) -> None:
        raw = await repository.find_comment(table_id, record_id, comment_id)
        if not raw:
            raise _comment_not_found()
        user_id = cls.get("user.id")
        data: list = []
        if raw["reaction"]:
            emojis = json.loads(raw["reaction"])
            index = next(
                (i for i, item in enumerate(emojis) if item["reaction"] == reaction), -1
            )
            if index > -1:
                new_user = [u for u in emojis[index]["user"] if u != user_id]
                if not new_user:
                    emojis.pop(index)
                else:
                    emojis[index] = {"reaction": reaction, "user": new_user}
                data = list(emojis)
        count = await repository.update_reaction(
            table_id,
            record_id,
            comment_id,
            json.dumps(data, ensure_ascii=False, separators=(",", ":")) if data else None,
            raw["lastModifiedTime"],
        )
        if not count:
            raise _comment_not_found()

    # -- subscription ----------------------------------------------------------
    async def get_subscribe_detail(
        self, table_id: str, record_id: str
    ) -> dict[str, Any] | None:
        return await repository.get_subscription(table_id, record_id)

    async def subscribe_comment(self, table_id: str, record_id: str) -> None:
        await repository.create_subscription(table_id, record_id, cls.get("user.id"))

    async def unsubscribe_comment(self, table_id: str, record_id: str) -> None:
        count = await repository.delete_subscription(table_id, record_id)
        if not count:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)

    # -- attachment ------------------------------------------------------------
    async def get_attachment_presigned_url(
        self, path: str
    ) -> str:
        token = path.split("/")[1] if "/" in path else path
        bucket = self._private_bucket()
        return self._preview_url_by_path(bucket, path, token)

    # -- notify (fan-out) ------------------------------------------------------
    async def _send_comment_notify(
        self,
        table_id: str,
        record_id: str,
        comment_id: str,
        quote_id: str | None,
        content: str | None,
    ) -> None:
        from_user_id = cls.get("user.id")
        relative_users: list[str] = []
        if quote_id:
            creator = await repository.find_comment_created_by(table_id, record_id, quote_id)
            if creator:
                relative_users.append(creator)
        relative_users.extend(self._mention_users(content))
        subscribers = await repository.list_subscribers(table_id, record_id)
        recipients = [
            uid
            for uid in dict.fromkeys([*subscribers, *relative_users])
            if uid != from_user_id
        ]
        if not recipients:
            return

        from ..field.repository import get_table_meta_by_id
        from ..notification.service import NotificationService

        table = await get_table_meta_by_id(table_id)
        if table is None:
            return
        base_id = table["base_id"]
        table_name = table.get("name") or ""
        from_users = await repository.get_users_by_ids([from_user_id])
        from_user_name = (from_users.get(from_user_id) or {}).get("name") or ""
        base_name = ""
        try:
            from ..base import repository as base_repository

            base_row = await base_repository.get_base_row(base_id)
            base_name = (base_row or {}).get("name") or ""
        except Exception:
            base_name = ""
        try:
            record_vo = await RecordService().get_record(table_id, record_id, "id")
            record_name = record_vo.get("name") or ""
        except ApiError:
            record_name = ""
        url_path = (
            f"/base/{base_id}/table/{table_id}?recordId={record_id}&commentId={comment_id}"
        )
        message = (
            f"{from_user_name} made a comment on {record_name} "
            f"in {table_name} in {base_name}"
        )
        message_i18n = json.dumps(
            {
                "i18nKey": "email.templates.notify.recordComment.message",
                "context": {
                    "fromUserName": from_user_name,
                    "recordName": record_name,
                    "tableName": table_name,
                    "baseName": base_name,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        notifier = NotificationService()
        for to_user_id in recipients:
            await notifier.create_and_push(
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                notify_type="comment",
                message=message,
                message_i18n=message_i18n,
                url_path=url_path,
            )

    def _mention_users(self, content_raw: str | None) -> list[str]:
        if not content_raw:
            return []
        content = json.loads(content_raw)
        out: list[str] = []
        for item in content:
            if item.get("type") == CommentNodeType.PARAGRAPH.value:
                for child in item.get("children", []):
                    if child.get("type") == CommentNodeType.MENTION.value:
                        out.append(child["value"])
        return out
