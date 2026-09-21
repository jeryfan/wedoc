"""Outbound mail: aiosmtplib transport + Jinja2 templates ported from the
upstream handlebars set, with the same degraded behaviour when SMTP is absent.

Contract points (mail-sender.service.ts):
- not configured (no BACKEND_MAIL_AUTH_USER/PASS): the mail is rendered and
  logged, never sent; callers always observe success
- ``ENABLE_EMAIL_CODE_CONSOLE=true`` logs verification codes in plaintext
- send-mail rate limit keys ``send-mail-rate-limit:{key}:{email}`` with
  ``rateLimit - 2`` seconds of TTL (2s network allowance), 429 on repeat
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, FileSystemLoader

from ..config import Settings, get_settings
from .cache import CacheService, get_cache
from .errors import ApiError, HttpErrorCode
from .storage import get_public_full_storage_url, path_join

logger = structlog.get_logger(__name__)

BRAND_NAME = "wedoc"
EMAIL_LOGO_TOKEN = "email-logo"

TEMPLATE_DIR = Path(__file__).parent / "mail_templates"

_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)


def build_email_from(sender: str, sender_name: str | None = None) -> str:
    if not sender_name:
        return sender
    return f"{sender_name} <{sender}>"


class MailType:
    VERIFY_CODE = "verifyCode"
    RESET_PASSWORD = "resetPassword"
    WAITLIST_INVITE = "waitlistInvite"


class MailTransporterType:
    NOTIFY = "notify"


class Mailer:
    def __init__(
        self,
        settings: Settings | None = None,
        cache: CacheService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._cache = cache or get_cache()

    # -- rate limit ----------------------------------------------------------
    async def check_send_mail_rate_limit(
        self,
        email: str,
        rate_limit_key: str,
        rate_limit: int,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        if rate_limit <= 0:
            return await fn()
        effective = rate_limit - 2  # 2 seconds for network latency
        key = f"send-mail-rate-limit:{rate_limit_key}:{email}"
        if await self._cache.get(key):
            raise ApiError(
                "Reached the rate limit of sending mail, "
                f"please try again after {effective} seconds",
                HttpErrorCode.TOO_MANY_REQUESTS,
                {"seconds": rate_limit},
            )
        result = await fn()
        await self._cache.set_detail(key, True, effective)
        return result

    # -- transport -------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return self._settings.is_mail_configured

    def render(self, partial_body: str, context: dict[str, Any]) -> str:
        template = _env.get_template("normal.html")
        return template.render(
            partial_body=f"partials/{partial_body}.html",
            public_origin=self._settings.public_origin,
            current_year=datetime.now(tz=UTC).year,
            **context,
        )

    async def send_mail(
        self,
        to: str,
        subject: str,
        partial_body: str,
        context: dict[str, Any],
    ) -> bool:
        html = self.render(partial_body, context)
        from_ = build_email_from(
            self._settings.backend_mail_sender, self._settings.backend_mail_sender_name
        )
        if not self.is_configured:
            logger.info(
                "[Mail Not Configured] Would send email",
                **{
                    "from": from_,
                    "to": to,
                    "subject": subject,
                    "template": "normal",
                    "context": context,
                    "body": html,
                },
            )
            return True
        from email.message import EmailMessage

        import aiosmtplib

        message = EmailMessage()
        message["From"] = from_
        message["To"] = to
        message["Subject"] = subject
        message.add_alternative(html, subtype="html")
        try:
            await aiosmtplib.send(
                message,
                hostname=self._settings.backend_mail_host,
                port=self._settings.backend_mail_port,
                username=self._settings.backend_mail_auth_user,
                password=self._settings.backend_mail_auth_pass,
                use_tls=self._settings.backend_mail_secure == "true",
            )
            return True
        except Exception as exc:
            logger.error("Mail sending failed", error=str(exc))
            return False

    # -- option builders (i18n strings from common-i18n en locale) -------------
    def _brand(self) -> dict[str, str]:
        return {
            "brand_name": BRAND_NAME,
            "brand_logo": get_public_full_storage_url(
                path_join("logo", EMAIL_LOGO_TOKEN), self._settings
            ),
        }

    async def send_signup_verification_code_mail(self, to: str, code: str, expires_in: str) -> bool:
        expires_minutes = parse_expires_minutes(expires_in)
        brand = self._brand()
        message = (
            f"Your verification code is {code}, "
            f"please use it within {expires_minutes} minutes."
        )
        if self._settings.enable_email_code_console:
            logger.info(f"signup Verification code: {code} expiresIn {expires_in}")
        return await self.send_mail(
            to,
            f"Signup Verification - {brand['brand_name']}",
            "email-verify-code",
            {**brand, "title": "Signup verification", "message": message},
        )

    async def send_change_email_code_mail(self, to: str, code: str, expires_in: str) -> bool:
        expires_minutes = parse_expires_minutes(expires_in)
        brand = self._brand()
        message = (
            f"Your verification code is {code}, "
            f"please use it within {expires_minutes} minutes."
        )
        if self._settings.enable_email_code_console:
            logger.info(f"changeEmail Verification code: {code} expiresIn {expires_in}")
        return await self.send_mail(
            to,
            f"Change Email Verification - {brand['brand_name']}",
            "email-verify-code",
            {**brand, "title": "Change email verification", "message": message},
        )

    async def send_reset_password_mail(
        self, to: str, name: str, reset_password_url: str
    ) -> bool:
        brand = self._brand()
        message = (
            "If you did not request this change, please ignore this email. "
            "Otherwise, click the button below to reset your password."
        )
        return await self.send_mail(
            to,
            f"Reset Password - {brand['brand_name']}",
            "reset-password",
            {
                **brand,
                "title": "Reset your password",
                "message": message,
                "button_text": "Reset password",
                "button_url": reset_password_url,
            },
        )

    async def send_waitlist_invite_mail(
        self, to: str, name: str, code: str, times: int, waitlist_invite_url: str
    ) -> bool:
        brand = self._brand()
        message = (
            f"You've successfully joined the waitlist of {brand['brand_name']}, "
            f"please use the following invite code to register: {code}, "
            f"it can be used {times} times."
        )
        return await self.send_mail(
            to,
            f"Welcome - {brand['brand_name']}",
            "common-body",
            {
                **brand,
                "title": "Welcome",
                "message": message,
                "button_text": "Register",
                "button_url": waitlist_invite_url,
            },
        )


def parse_expires_minutes(expires_in: str) -> int:
    from .duration import parse_ms

    return parse_ms(expires_in) // 60000


_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        _mailer = Mailer()
    return _mailer


def reset_mailer() -> None:
    global _mailer
    _mailer = None
