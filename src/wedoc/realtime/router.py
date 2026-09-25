"""SockJS HTTP + websocket routes under the ``/socket`` prefix."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocket

from .sockjs import info_payload, websocket_endpoint

router = APIRouter()

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
}


@router.get("/socket/info")
async def socket_info() -> JSONResponse:
    return JSONResponse(info_payload(), headers=_CORS_HEADERS)


@router.options("/socket/info")
async def socket_info_options() -> Response:
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS, GET",
            "Access-Control-Max-Age": "31536000",
        },
    )


@router.websocket("/socket/{server}/{session}/websocket")
async def socket_websocket(websocket: WebSocket, server: str, session: str) -> None:
    await websocket_endpoint(websocket)
