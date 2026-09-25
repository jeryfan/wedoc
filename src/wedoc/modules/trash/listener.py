"""Trash recorder — ports event-emitter/listeners/trash.listener.ts.

Upstream reacts to SPACE_DELETE / BASE_DELETE / TABLE_DELETE and upserts a
trash row (idempotent on unique(resourceType, resourceId)). wedoc has no event
bus, so the soft-delete services call ``record_resource_deleted`` directly.
"""

import uuid
from datetime import datetime

from ...core import cls
from ...core.ids import cuid
from . import repository


def _trash_id(resource_type: str) -> str:
    # Observed upstream: space/base delete rows carry a cuid, table delete rows a
    # UUID (the table delete path mints its trash id differently). Match both.
    if resource_type == "table":
        return str(uuid.uuid4())
    return cuid()


async def record_resource_deleted(
    resource_type: str,
    resource_id: str,
    parent_id: str | None,
    deleted_time: datetime,
) -> None:
    await repository.upsert_trash(
        _trash_id(resource_type),
        resource_type,
        resource_id,
        parent_id,
        deleted_time,
        cls.get("user.id"),
    )
