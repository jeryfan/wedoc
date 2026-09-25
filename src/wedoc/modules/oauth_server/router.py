"""Routes for /api/oauth (authorization server) — ports oauth-server.controller.ts."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard, ensure_login
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .service import (
    DEVICE_CODE_GRANT_TYPE,
    DeviceAuthorizationError,
    OAuthDeviceService,
    OAuthServerService,
    OAuthTokenError,
)

# --- public: device code + token exchange ---------------------------------
public_router = APIRouter(prefix="/api/oauth")


async def _form_or_json(request: Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type") or ""
    if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
        form = await request.form()
        return {k: v for k, v in form.items()}
    return await read_json_body(request)


@public_router.post("/device/code")
async def device_code(request: Request) -> JSONResponse:
    body = await _form_or_json(request)
    client_id = body.get("client_id")
    scope = body.get("scope")
    scope = scope.strip() if isinstance(scope, str) else None
    try:
        result = await OAuthDeviceService().request_device_code(client_id, scope)
    except DeviceAuthorizationError as exc:
        return JSONResponse(
            {"error": exc.rfc_error, "error_description": exc.description}, status_code=400
        )
    return JSONResponse(result, status_code=200)


@public_router.post("/access_token")
async def access_token(request: Request) -> Response:
    body = await _form_or_json(request)
    client_id = body.get("client_id")
    client_secret = body.get("client_secret")
    code_verifier = body.get("code_verifier")
    service = OAuthServerService()
    try:
        client = await service.authenticate_client(client_id, client_secret, code_verifier)
    except OAuthTokenError as exc:
        raise ApiError(exc.message, _status_code(exc.status)) from None
    grant_type = body.get("grant_type")
    try:
        if grant_type == "authorization_code":
            result = await service.exchange_authorization_code(
                client, body.get("code"), body.get("redirect_uri")
            )
            return JSONResponse(result, status_code=201)
        if grant_type == "refresh_token":
            result = await service.exchange_refresh_token(client, body.get("refresh_token"))
            return JSONResponse(result, status_code=201)
        if grant_type == DEVICE_CODE_GRANT_TYPE:
            result = await service.exchange_device_code(client, body.get("device_code"))
            if "_rfc_error" in result:
                payload = {"error": result["_rfc_error"]}
                if result.get("error_description"):
                    payload["error_description"] = result["error_description"]
                return JSONResponse(payload, status_code=400)
            return JSONResponse(result, status_code=200)
    except OAuthTokenError as exc:
        raise ApiError(exc.message, _status_code(exc.status)) from None
    # unknown/missing grant_type reaches oauth2orize's fallthrough → 500 on ref
    raise RuntimeError("Unsupported grant type")


def _status_code(status: int) -> HttpErrorCode:
    if status == 401:
        return HttpErrorCode.UNAUTHORIZED
    if status == 404:
        return HttpErrorCode.NOT_FOUND
    return HttpErrorCode.VALIDATION_ERROR


# --- @EnsureLogin gated ----------------------------------------------------
ensure_router = APIRouter(
    prefix="/api/oauth", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


@ensure_router.get("/authorize")
@ensure_login()
async def authorize(request: Request) -> Response:
    user = cls.get("user")
    try:
        result = await OAuthServerService().build_authorize_transaction(
            dict(request.query_params),
            {"id": user["id"], "name": user["name"], "email": user["email"]},
        )
    except OAuthTokenError as exc:
        code = HttpErrorCode.UNAUTHORIZED if exc.status == 401 else HttpErrorCode.VALIDATION_ERROR
        raise ApiError(exc.message, code) from None
    if result["immediate"]:
        location = result["redirect"]
    else:
        location = f"/oauth/decision?transaction_id={result['transactionId']}"
    return Response(status_code=302, headers={"location": location})


@ensure_router.post("/decision")
@ensure_login()
async def decision(request: Request) -> Response:
    body = await _form_or_json(request)
    transaction_id = body.get("transaction_id")
    # oauth2orize denies when the consent form posts a `cancel` field.
    allow = "cancel" not in body
    user = cls.get("user")
    location = await OAuthServerService().decision(
        transaction_id,
        allow,
        {"id": user["id"], "name": user["name"], "email": user["email"]},
    )
    return Response(status_code=302, headers={"location": location})


@ensure_router.get("/device/{user_code}")
@ensure_login()
async def device_app(user_code: str) -> dict[str, Any]:
    return await OAuthDeviceService().get_device_app(user_code)


@ensure_router.post("/device/decision")
@ensure_login()
async def device_decision(request: Request) -> Response:
    body = await read_json_body(request)
    body = body if isinstance(body, dict) else {}
    issues: list[str] = []
    if not isinstance(body.get("userCode"), str):
        issues.append('Invalid input: expected string, received undefined at "userCode"')
    if not isinstance(body.get("approve"), bool):
        issues.append('Invalid input: expected boolean, received undefined at "approve"')
    if issues:
        raise ApiError("Validation error: " + "; ".join(issues), HttpErrorCode.VALIDATION_ERROR)
    user = cls.get("user")
    await OAuthServerService().decide_device(
        body["userCode"],
        body["approve"],
        {"id": user["id"], "name": user["name"], "email": user["email"]},
    )
    return Response(status_code=201)


# --- auth required (no ensure-login redirect) ------------------------------
auth_router = APIRouter(
    prefix="/api/oauth", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


@auth_router.get("/decision/{transaction_id}")
async def transaction(transaction_id: str) -> dict[str, Any]:
    return await OAuthServerService().get_decision_info(transaction_id)
