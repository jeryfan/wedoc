"""AuthController + LocalAuthController port: /api/auth routes.

Status codes follow Nest defaults per route (POST=201 unless @HttpCode) —
each route pins ``status_code`` explicitly for that reason.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.i18n import resolve_lang
from ...core.security.auth import (
    AllowAnonymousType,
    allow_anonymous,
    auth_guard,
    permissions,
    pick_user_me,
    public,
    token_access,
)
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from ..user.delete_user import DeleteUserService
from .schemas import (
    AddPasswordBody,
    ChangeEmailBody,
    ChangePasswordBody,
    DeleteUserQuery,
    InviteWaitlistBody,
    JoinWaitlistBody,
    ResetPasswordBody,
    SendChangeEmailCodeBody,
    SendResetPasswordEmailBody,
    SendSignupVerificationCodeBody,
    SignupBody,
    WaitlistInviteCodeBody,
)
from .service import AuthService, session_login, session_logout

router = APIRouter(
    prefix="/api/auth",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _empty(status_code: int) -> Response:
    return Response(status_code=status_code)


_AUTH_GUARD = Depends(auth_guard)


@router.post("/signin", status_code=200)
@public()
async def signin(request: Request, response: Response) -> dict[str, Any]:
    body = await read_json_body(request)
    email = body.get("email") if isinstance(body, dict) else None
    password = body.get("password") if isinstance(body, dict) else None
    if not email or not password:
        raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED)
    user = await AuthService().signin(email, password)
    await session_login(request, response, user["id"])
    return user


@router.post("/signup", status_code=201)
@public()
async def signup(request: Request, response: Response) -> dict[str, Any]:
    body = SignupBody.zod_validate(await read_json_body(request))
    user = await AuthService().signup(body, lang=resolve_lang(request))
    await session_login(request, response, user["id"])
    return pick_user_me(user)


@router.post("/signout", status_code=200)
@allow_anonymous(AllowAnonymousType.USER)
async def signout(request: Request) -> Response:
    response = _empty(200)
    await session_logout(request, response)
    return response


@router.post("/send-signup-verification-code", status_code=200)
@public()
async def send_signup_verification_code(request: Request) -> dict[str, Any]:
    body = SendSignupVerificationCodeBody.zod_validate(await read_json_body(request))
    return await AuthService().send_signup_verification_code(body.email)


@router.post("/join-waitlist", status_code=201)
@public()
async def join_waitlist(request: Request) -> dict[str, Any]:
    body = JoinWaitlistBody.zod_validate(await read_json_body(request))
    await AuthService().join_waitlist(body.email)
    return {"email": body.email}


@router.post("/invite-waitlist", status_code=201)
@permissions("instance|update")
async def invite_waitlist(request: Request) -> list[dict[str, Any]]:
    body = InviteWaitlistBody.zod_validate(await read_json_body(request))
    return await AuthService().invite_waitlist(body.list)


def _waitlist_iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat(timespec="milliseconds") + "Z"
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@router.get("/waitlist", status_code=200)
@permissions("instance|read")
async def get_waitlist() -> list[dict[str, Any]]:
    rows = await AuthService().get_waitlist()
    return [
        {
            "email": row["email"],
            "invite": row["invite"],
            "inviteTime": _waitlist_iso(row["invite_time"]),
            "createdTime": _waitlist_iso(row["created_time"]),
        }
        for row in rows
    ]


@router.post("/waitlist-invite-code", status_code=201)
@permissions("instance|update")
async def gen_waitlist_invite_code(request: Request) -> list[dict[str, Any]]:
    body = WaitlistInviteCodeBody.zod_validate(await read_json_body(request))
    times = max(body.times, 1)
    service = AuthService()
    result = []
    for _ in range(body.count):
        code = await service.gen_waitlist_invite_code(times)
        result.append({"code": code, "times": times})
    return result


@router.patch("/change-password", status_code=200)
async def change_password(request: Request) -> Response:
    body = ChangePasswordBody.zod_validate(await read_json_body(request))
    await AuthService().change_password(cls.get("user.id"), body.password, body.newPassword)
    response = _empty(200)
    await session_logout(request, response)
    return response


@router.post("/send-reset-password-email", status_code=201)
@public()
async def send_reset_password_email(request: Request) -> Response:
    body = SendResetPasswordEmailBody.zod_validate(await read_json_body(request))
    await AuthService().send_reset_password_email(body.email)
    return _empty(201)


@router.post("/reset-password", status_code=201)
@public()
async def reset_password(request: Request) -> Response:
    body = ResetPasswordBody.zod_validate(await read_json_body(request))
    await AuthService().reset_password(body.code, body.password)
    response = _empty(201)
    await session_logout(request, response)
    return response


@router.post("/add-password", status_code=201)
async def add_password(request: Request) -> Response:
    body = AddPasswordBody.zod_validate(await read_json_body(request))
    await AuthService().add_password(cls.get("user.id"), body.password)
    response = _empty(201)
    await session_logout(request, response)
    return response


@router.patch("/change-email", status_code=200)
async def change_email(request: Request) -> Response:
    body = ChangeEmailBody.zod_validate(await read_json_body(request))
    await AuthService().change_email(body.email, body.token, body.code)
    response = _empty(200)
    await session_logout(request, response)
    return response


@router.post("/send-change-email-code", status_code=200)
async def send_change_email_code(request: Request) -> dict[str, str]:
    body = SendChangeEmailCodeBody.zod_validate(await read_json_body(request))
    return await AuthService().send_change_email_code(body.email, body.password)


@router.get("/user/me", status_code=200)
@allow_anonymous(AllowAnonymousType.USER)
async def user_me(user: dict[str, Any] = _AUTH_GUARD) -> dict[str, Any]:
    result = dict(user)
    organization = cls.get("organization")
    if organization is not None:
        result["organization"] = organization
    return result


@router.get("/user", status_code=200)
@token_access()
async def get_user(user: dict[str, Any] = _AUTH_GUARD) -> dict[str, Any]:
    return await AuthService().get_user_info(user)


@router.get("/temp-token", status_code=200)
async def temp_token() -> dict[str, Any]:
    return await AuthService().get_temp_token()


@router.delete("/user", status_code=200)
async def delete_user(request: Request) -> Response:
    DeleteUserQuery.zod_validate(dict(request.query_params))
    await DeleteUserService().delete_user()
    response = _empty(200)
    await session_logout(request, response)
    return response
