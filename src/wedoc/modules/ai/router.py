"""Routes for /api/:baseId/ai and /api/chart — ports ai/chat controllers."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import AiGenerateRo
from .service import AiService, ChatService

router = APIRouter(
    prefix="/api/{baseId}/ai",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("/generate-stream", status_code=201)
@permissions("base|read")
async def generate_stream(baseId: str, request: Request) -> Response:
    ro = AiGenerateRo.zod_validate(await read_json_body(request))
    await AiService().generate_stream(baseId, ro)
    return Response(status_code=201)


@router.get("/config", status_code=200)
@permissions("base|read")
async def get_ai_config(baseId: str) -> Any:
    result = await AiService().get_simplified_ai_config(baseId)
    return result if result is not None else Response(status_code=200)


@router.get("/disable-ai-actions", status_code=200)
@permissions("base|read")
async def get_ai_disable_ai_actions(baseId: str) -> dict[str, Any]:
    return await AiService().get_ai_disable_ai_actions(baseId)


chat_router = APIRouter(
    prefix="/api/chart", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


@chat_router.post("/completions", status_code=201)
async def completions(request: Request) -> Response:
    await ChatService().completions()
    return Response(status_code=201)
