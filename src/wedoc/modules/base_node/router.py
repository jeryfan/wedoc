"""Routes for /api/base/:baseId/node (+ /node/folder).

Ports base-node.controller.ts and folder/base-node-folder.controller.ts.
Node-level permission configuration checks (BaseNodePermissionGuard) are not
registered — with no base_node_permission rows upstream allows everything.
Table/dashboard/workflow/app resource types arrive with the table/dashboard
modules; the node-list reconciliation will pick their rows up automatically.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    DASHBOARD,
    TABLE,
    CreateDashboardNodeBody,
    CreateFolderBody,
    CreateNodeBody,
    CreateTableNodeBody,
    MoveNodeBody,
    UpdateFolderBody,
    UpdateNodeBody,
)
from .service import BaseNodeService, create_folder, delete_folder, rename_folder

router = APIRouter(
    prefix="/api/base/{baseId}/node",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("/list", status_code=200)
@permissions("base|read")
async def get_list(baseId: str) -> list[dict[str, Any]]:
    return await BaseNodeService().get_list(baseId)


@router.get("/tree", status_code=200)
@permissions("base|read")
async def get_tree(baseId: str) -> dict[str, Any]:
    return await BaseNodeService().get_tree(baseId)


@router.post("/folder", status_code=201)
@permissions("base|update")
async def create_folder_route(baseId: str, request: Request) -> dict[str, Any]:
    body = CreateFolderBody.zod_validate(await read_json_body(request))
    return await create_folder(baseId, body.name)


@router.patch("/folder/{folderId}", status_code=200)
@permissions("base|update")
async def rename_folder_route(folderId: str, baseId: str, request: Request) -> dict[str, Any]:
    body = UpdateFolderBody.zod_validate(await read_json_body(request))
    return await rename_folder(baseId, folderId, body.name)


@router.delete("/folder/{folderId}", status_code=200)
@permissions("base|update")
async def delete_folder_route(folderId: str, baseId: str) -> Response:
    # upstream returns an empty 200 body for folder deletion.
    await delete_folder(baseId, folderId)
    return Response(status_code=200)


@router.get("/{nodeId}", status_code=200)
@permissions("base|read")
async def get_node(baseId: str, nodeId: str) -> dict[str, Any]:
    return await BaseNodeService().get_node_vo(baseId, nodeId)


@router.post("", status_code=201)
@permissions("base|read")
async def create_node(baseId: str, request: Request) -> dict[str, Any]:
    raw = await read_json_body(request)
    resource_type = raw.get("resourceType") if isinstance(raw, dict) else None
    # createBaseNodeRoSchema is a discriminated union on resourceType: the table
    # branch spreads the full create-table RO, the dashboard branch the dashboard
    # RO. Pick the matching schema so fields/views (etc.) survive validation.
    if resource_type == TABLE:
        body: Any = CreateTableNodeBody.zod_validate(raw)
    elif resource_type == DASHBOARD:
        body = CreateDashboardNodeBody.zod_validate(raw)
    else:
        body = CreateNodeBody.zod_validate(raw)
    return await BaseNodeService().create(baseId, body)


@router.post("/{nodeId}/duplicate", status_code=201)
@permissions("base|read")
async def duplicate_node(baseId: str, nodeId: str, request: Request) -> dict[str, Any]:
    # body is a union (table | dashboard | workflow/app) — pass the raw payload so
    # table duplication keeps includeRecords, resolved per anchor resourceType.
    body = await read_json_body(request)
    return await BaseNodeService().duplicate(baseId, nodeId, body)


@router.put("/{nodeId}", status_code=200)
@permissions("base|read")
async def update_node(baseId: str, nodeId: str, request: Request) -> dict[str, Any]:
    body = UpdateNodeBody.zod_validate(await read_json_body(request))
    return await BaseNodeService().update(baseId, nodeId, body)


@router.put("/{nodeId}/move", status_code=200)
@permissions("base|update")
async def move_node(baseId: str, nodeId: str, request: Request) -> dict[str, Any]:
    body = MoveNodeBody.zod_validate(await read_json_body(request))
    return await BaseNodeService().move(baseId, nodeId, body)


@router.delete("/{nodeId}", status_code=200)
@permissions("base|read")
async def delete_node(baseId: str, nodeId: str) -> dict[str, Any]:
    return await BaseNodeService().delete(baseId, nodeId)


@router.delete("/{nodeId}/permanent", status_code=200)
@permissions("base|read")
async def permanent_delete_node(baseId: str, nodeId: str) -> dict[str, Any]:
    result = await BaseNodeService().delete(baseId, nodeId, permanent=True)
    return {**result, "permanent": True}
