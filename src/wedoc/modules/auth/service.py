"""AuthService + LocalAuthService port: signup/signin, password and email
lifecycle, waitlist, temp tokens.

Every error's ``data.localization.i18nKey`` mirrors the upstream
CustomHttpException payloads — they are part of the wire contract.
"""

import json
import time
from datetime import UTC, datetime
from typing import Any

import bcrypt
import jwt as pyjwt
import structlog
from fastapi import Request, Response

from ...config import Settings, get_settings
from ...core import cls
from ...core.cache import CacheService, get_cache, second
from ...core.duration import parse_ms
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id, random_string
from ...core.mailer import Mailer, get_mailer
from ...core.security.auth import JwtService, pick_user_me
from ...core.security.session import (
    SessionHandle,
    SessionStore,
    build_clear_cookie_header,
    build_set_cookie_header,
    generate_sid,
    new_session_data,
    session_cookie_secure,
)
from ..user import repository as user_repository
from ..user.service import UserService
from . import repository as waitlist_repository
from .schemas import SignupBody

logger = structlog.get_logger(__name__)

BCRYPT_ROUNDS = 10


def encode_password(password: str) -> tuple[str, str]:
    """bcrypt with node-compatible 72-byte input truncation."""
    salt = bcrypt.gensalt(BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password.encode()[:72], salt)
    return salt.decode(), hashed.decode()


def compare_password(password: str | None, hash_password: str | None, salt: str | None) -> bool:
    """bcrypt.hash(password || '', salt || '') === hashPassword; invalid salt raises."""
    computed = bcrypt.hashpw((password or "").encode()[:72], (salt or "").encode()).decode()
    return computed == (hash_password or "")


def _iso_ms(epoch_ms: float) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


async def session_login(request: Request, response: Response, user_id: str) -> None:
    """passport req.login: regenerate the sid and persist the session."""
    store = SessionStore(get_cache())
    handle = SessionHandle(store)
    old_sid = handle.session_id_from_cookie_header(request.headers.get("cookie"))
    if old_sid:
        await store.destroy(old_sid)
    secure_option = session_cookie_secure()
    secure: bool | None
    if secure_option == "auto":
        secure = request.url.scheme == "https"
    else:
        secure = secure_option
    sid = generate_sid()
    await store.set(sid, new_session_data(user_id, secure))
    response.headers.append(
        "set-cookie", build_set_cookie_header(sid, secure=secure if secure else None)
    )


async def session_logout(request: Request, response: Response) -> None:
    store = SessionStore(get_cache())
    handle = SessionHandle(store)
    sid = handle.session_id_from_cookie_header(request.headers.get("cookie"))
    if sid:
        await store.destroy(sid)
    response.headers.append("set-cookie", build_clear_cookie_header())


async def clear_user_sessions(user_id: str) -> None:
    await SessionStore(get_cache()).clear_by_user_id(user_id)


class AuthService:
    def __init__(
        self,
        settings: Settings | None = None,
        cache: CacheService | None = None,
        mailer: Mailer | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._cache = cache or get_cache()
        self._mailer = mailer or get_mailer()
        self._jwt = JwtService()
        self._users = UserService(self._settings)

    # -- signup / signin -----------------------------------------------------

    async def validate_user_by_email(self, email: str, password: str) -> dict[str, Any] | None:
        user = await self._users.get_user_by_email(email)
        if not user or (not user["accounts"] and user["password"] is None):
            raise ApiError(
                f"{email} not registered",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.emailNotRegistered"}},
            )
        if not user["password"]:
            raise ApiError(
                "Password is not set",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.passwordNotSet"}},
            )
        if user["is_system"]:
            raise ApiError(
                "User is system user",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.systemUser"}},
            )
        if compare_password(password, user["password"], user["salt"]):
            return user
        return None

    async def signin(self, email: Any, password: Any) -> dict[str, Any]:
        """LocalStrategy.validate: any failure becomes invalid_credentials (or
        the lockout error when SIGNIN_* lockout envs are set)."""
        invalid = ApiError(
            "Email or password is incorrect",
            HttpErrorCode.INVALID_CREDENTIALS,
            {"localization": {"i18nKey": "httpErrors.auth.emailOrPasswordIncorrect"}},
        )
        max_attempts = self._settings.signin_max_login_attempts
        lockout_minutes = self._settings.signin_account_lockout_minutes
        has_lockout = bool(max_attempts and lockout_minutes)
        try:
            user = await self.validate_user_by_email(email, password)
            if not user:
                raise invalid
            if user["deactivated_time"]:
                raise ApiError(
                    "Your account has been deactivated by the administrator",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.auth.accountDeactivated"}},
                )
            await self._users.refresh_last_sign_time(user["id"])
        except Exception as error:
            if not has_lockout:
                raise invalid from error
            lock_error = ApiError(
                "Your account has been locked out, "
                f"please try again after {lockout_minutes} minutes",
                HttpErrorCode.TOO_MANY_REQUESTS,
                {
                    "minutes": lockout_minutes,
                    "localization": {"i18nKey": "httpErrors.auth.accountLockedOut"},
                },
            )
            if await self._cache.get(f"signin:lockout:{email}"):
                raise lock_error from error
            attempts = await self._cache.incr(f"signin:attempts:{email}", 30)
            if attempts >= max_attempts:
                await self._cache.set(f"signin:lockout:{email}", True, lockout_minutes)
                await self._cache.expire(f"signin:attempts:{email}", 1)
                raise lock_error from error
            raise ApiError(
                "Email or password is incorrect",
                HttpErrorCode.INVALID_CREDENTIALS,
                {
                    "attempts": attempts,
                    "localization": {"i18nKey": "httpErrors.auth.emailOrPasswordIncorrect"},
                },
            ) from error
        return pick_user_me(user)

    def _jwt_signup_code(self, email: str, code: str) -> str:
        return self._jwt.sign(
            {"email": email, "code": code},
            expires_in=self._settings.signup_verification_expires_in_resolved,
        )

    def _jwt_verify_signup_code(self, token: str) -> dict[str, Any]:
        try:
            return self._jwt.verify(token)
        except pyjwt.PyJWTError as error:
            raise ApiError(
                "Verification code is invalid", HttpErrorCode.INVALID_CAPTCHA
            ) from error

    async def _verify_signup(self, body: SignupBody) -> None:
        setting = await self._users.get_setting()
        if not setting.get("enableEmailVerification"):
            return
        verification = body.verification
        if verification is None:
            result = await self.send_signup_verification_code(body.email)
            raise ApiError(
                "Verification is required",
                HttpErrorCode.UNPROCESSABLE_ENTITY,
                {"token": result["token"], "expiresTime": result["expiresTime"]},
            )
        payload = self._jwt_verify_signup_code(verification.token)
        if payload.get("email") != body.email or payload.get("code") != verification.code:
            raise ApiError("Verification code is invalid", HttpErrorCode.INVALID_CAPTCHA)

    def _is_registered_validate(self, user: dict[str, Any] | None) -> None:
        if user and (user["password"] is not None or user["accounts"]):
            raise ApiError(
                f"User {user['email']} is already registered",
                HttpErrorCode.CONFLICT,
                {"localization": {"i18nKey": "httpErrors.auth.alreadyRegistered"}},
            )
        if user and user["is_system"]:
            raise ApiError(
                f"User {user['email']} is system user",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.systemUser"}},
            )

    async def signup(self, body: SignupBody, lang: str | None = None) -> dict[str, Any]:
        await self._verify_signup(body)
        user = await self._users.get_user_by_email(body.email)
        self._is_registered_validate(user)
        salt, hash_password = encode_password(body.password)
        ref_meta = None
        if body.refMeta is not None:
            ref_meta_dict = body.refMeta.model_dump(exclude_unset=True)
            if ref_meta_dict:
                ref_meta = json.dumps(ref_meta_dict, separators=(",", ":"))
        if user:
            data: dict[str, Any] = {
                "salt": salt,
                "password": hash_password,
                "last_sign_time": _now(),
            }
            if ref_meta is not None:
                data["ref_meta"] = ref_meta
            await user_repository.update_user_row(user["id"], data)
            updated = await self._users.get_user_by_id(user["id"])
            assert updated is not None
            return updated
        return await self._users.create_user_with_setting_check(
            {
                "id": new_id(IdPrefix.USER),
                "name": body.email.split("@")[0],
                "email": body.email,
                "salt": salt,
                "password": hash_password,
                "last_sign_time": _now(),
                "ref_meta": ref_meta,
            },
            default_space_name=body.defaultSpaceName,
            invite_code=body.inviteCode,
            lang=lang,
        )

    # -- signup verification code ----------------------------------------------

    async def send_signup_verification_code(self, email: str) -> dict[str, Any]:
        setting = await self._users.get_setting()
        self._users.throw_if_email_domain_banned(email, setting.get("bannedEmailDomains"))

        async def send() -> dict[str, Any]:
            code = random_string(4, numeric=True)
            token = self._jwt_signup_code(email, code)
            user = await self._users.get_user_by_email(email)
            self._is_registered_validate(user)
            await self._mailer.send_signup_verification_code_mail(
                email, code, self._settings.signup_verification_expires_in_resolved
            )
            ttl = parse_ms(self._settings.signup_verification_expires_in_resolved)
            return {
                "token": token,
                "expiresTime": _iso_ms(time.time() * 1000 + ttl),
            }

        return await self._mailer.check_send_mail_rate_limit(
            email,
            "signup-verification",
            self._settings.signup_verification_send_code_mail_rate,
            send,
        )

    # -- password lifecycle ----------------------------------------------------

    async def _get_user_or_404(self, user_id: str) -> dict[str, Any]:
        user = await self._users.get_user_by_id(user_id)
        if not user:
            raise ApiError(
                "User not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.user.notFound"}},
            )
        return user

    async def change_password(self, user_id: str, password: str, new_password: str) -> None:
        user = await self._get_user_or_404(user_id)
        if not compare_password(password, user["password"], user["salt"]):
            raise ApiError(
                "Password is incorrect",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.passwordIncorrect"}},
            )
        salt, hash_password = encode_password(new_password)
        await user_repository.update_user_row(
            user_id, {"password": hash_password, "salt": salt}
        )
        await clear_user_sessions(user_id)

    async def send_reset_password_email(self, email: str) -> None:
        async def send() -> None:
            user = await self._users.get_user_by_email(email)
            if not user or (not user["accounts"] and user["password"] is None):
                raise ApiError(
                    f"{email} not registered",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.auth.emailNotRegistered"}},
                )
            code = random_string(30)
            url = f"{self._settings.public_origin}/auth/reset-password?code={code}"
            await self._mailer.send_reset_password_mail(user["email"], user["name"], url)
            await self._cache.set(
                f"reset-password-email:{code}",
                {"userId": user["id"]},
                second(self._settings.reset_password_email_expires_in_resolved),
            )

        await self._mailer.check_send_mail_rate_limit(
            email,
            "send-reset-password-email",
            self._settings.backend_reset_password_send_mail_rate,
            send,
        )

    async def reset_password(self, code: str, password: str) -> str:
        key = f"reset-password-email:{code}"
        entry = await self._cache.get(key)
        if not entry:
            raise ApiError(
                "Token is invalid",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.tokenInvalid"}},
            )
        consumed = await self._cache.delete(key)
        if not consumed:
            raise ApiError(
                "Token is invalid",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.tokenInvalid"}},
            )
        user_id = entry["userId"]
        salt, hash_password = encode_password(password)
        await user_repository.update_user_row(
            user_id, {"password": hash_password, "salt": salt}
        )
        await clear_user_sessions(user_id)
        return user_id

    async def add_password(self, user_id: str, password: str) -> None:
        user = await self._get_user_or_404(user_id)
        if user["password"]:
            raise ApiError(
                "Password is already set",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.passwordAlreadyExists"}},
            )
        salt, hash_password = encode_password(password)
        from ..user import repository as user_repository

        await user_repository.update_user_row(
            user_id, {"password": hash_password, "salt": salt}, only_with_password_null=True
        )
        await clear_user_sessions(user_id)

    # -- email change ------------------------------------------------------------

    async def change_email(self, email: str, token: str, code: str) -> None:
        current_email = cls.get("user.email")
        invalid = ApiError("Verification code is invalid", HttpErrorCode.INVALID_CAPTCHA)
        try:
            payload = self._jwt.verify(token)
        except pyjwt.PyJWTError as error:
            raise invalid from error
        if (
            str(payload.get("newEmail", "")).lower() != email.lower()
            or payload.get("email") != current_email
            or payload.get("code") != code
        ):
            raise ApiError(
                "Verification code is invalid",
                HttpErrorCode.INVALID_CAPTCHA,
                {"localization": {"i18nKey": "httpErrors.auth.verificationCodeInvalid"}},
            )
        user_id = cls.get("user.id")
        await user_repository.update_user_row(
            user_id, {"email": str(payload["newEmail"]).lower()}
        )
        await clear_user_sessions(user_id)

    async def send_change_email_code(self, new_email: str, password: str) -> dict[str, Any]:
        email = cls.get("user.email")
        if new_email.lower() == email.lower():
            raise ApiError(
                "New email is the same as the current email",
                HttpErrorCode.CONFLICT,
                {"localization": {"i18nKey": "httpErrors.auth.newEmailSameAsCurrentEmail"}},
            )
        invalid_password = ApiError(
            "Password is incorrect",
            HttpErrorCode.INVALID_CREDENTIALS,
            {"localization": {"i18nKey": "httpErrors.auth.passwordIncorrect"}},
        )

        async def send() -> dict[str, str]:
            try:
                user = await self.validate_user_by_email(email, password)
            except Exception:
                raise invalid_password from None
            if not user:
                raise invalid_password
            if await self._users.get_user_by_email(new_email):
                raise ApiError(
                    "New email is already registered",
                    HttpErrorCode.CONFLICT,
                    {"localization": {"i18nKey": "httpErrors.auth.emailAlreadyRegistered"}},
                )
            code = random_string(4, numeric=True)
            token = self._jwt.sign(
                {"email": email, "newEmail": new_email, "code": code},
                expires_in=self._settings.backend_email_code_expires_in,
            )
            await self._mailer.send_change_email_code_mail(
                new_email, code, self._settings.backend_email_code_expires_in
            )
            return {"token": token}

        return await self._mailer.check_send_mail_rate_limit(
            new_email,
            "send-change-email-code",
            self._settings.backend_change_email_send_code_mail_rate,
            send,
        )

    # -- waitlist ------------------------------------------------------------------

    async def join_waitlist(self, email: str) -> dict[str, Any]:
        setting = await self._users.get_setting()
        if not setting.get("enableWaitlist"):
            raise ApiError(
                "Waitlist is not enabled",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.auth.waitlistNotEnabled"}},
            )
        if await self._users.get_user_by_email(email):
            raise ApiError(
                "Email already registered",
                HttpErrorCode.CONFLICT,
                {"localization": {"i18nKey": "httpErrors.auth.emailAlreadyRegistered"}},
            )
        found = await waitlist_repository.find_by_email(email)
        if found:
            return found
        return await waitlist_repository.create(email)

    async def get_waitlist(self) -> list[dict[str, Any]]:
        return await waitlist_repository.list_all()

    async def gen_waitlist_invite_code(self, limit: int) -> str:
        code = f"{random_string(4)}-{random_string(4)}"
        await self._cache.set(f"waitlist:invite-code:{code}", limit, "30d")
        return code

    async def invite_waitlist(self, emails: list[str]) -> list[dict[str, Any]]:
        pending = await waitlist_repository.find_uninvited_by_emails(emails)
        if not pending:
            return []
        await waitlist_repository.mark_invited([item["email"] for item in pending])
        result: list[dict[str, Any]] = []
        for item in pending:
            times = 10
            code = await self.gen_waitlist_invite_code(times)
            await self._mailer.send_waitlist_invite_mail(
                item["email"],
                "Guest",
                code,
                times,
                f"{self._settings.public_origin}/auth/signup?inviteCode={code}",
            )
            result.append({"email": item["email"], "code": code, "times": times})
        return result

    # -- misc ------------------------------------------------------------------

    async def get_user_info(self, user: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: user.get(key) for key in ("id", "email", "avatar", "name")
        }
        access_token_id = cls.get("accessTokenId")
        if not access_token_id:
            return result
        from ...core.security.permissions import PermissionService

        token = await PermissionService().get_access_token(access_token_id)
        if "user|email_read" not in token["scopes"]:
            result.pop("email")
        return result

    async def get_temp_token(self) -> dict[str, Any]:
        expires_in = "10m"
        payload: dict[str, Any] = {"userId": cls.get("user.id")}
        return {
            "accessToken": self._jwt.sign(payload, expires_in=expires_in),
            "expiresTime": _iso_ms(time.time() * 1000 + parse_ms(expires_in)),
        }
