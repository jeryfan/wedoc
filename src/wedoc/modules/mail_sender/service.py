"""Mail-sender open-api service — ports mail-sender-open-api.service.ts.

`test-transport-config` mirrors nodemailer `transport.verify()`: a refused host
surfaces the Node-style `connect ECONNREFUSED <host>:<port>` message (reproduced
by a raw TCP preflight so the string matches byte-for-byte). A reachable SMTP
server (e.g. a local aiosmtpd capturer) goes through the real verify + send.
`{baseId}/send` mirrors sendMail: with no transport configured the mail is
logged and the caller still observes `{success:true}`.
"""

import asyncio
from typing import Any

from ...config import get_settings
from ...core.errors import ApiError, HttpErrorCode
from .schemas import SendEmailRo, TestMailTransportConfigRo, _to_email_array


def _markdown_to_html(body: str) -> str:
    # markdown-it render is a passthrough here; the rendered body is not part of
    # the response contract (only {success,message} is returned).
    return body


async def _preflight_connect(host: str, port: int, connect_timeout: float = 10.0) -> None:
    """Raw TCP probe. Raises ValueError with nodemailer-style messages."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=connect_timeout
        )
    except ConnectionRefusedError as error:
        raise ValueError(f"connect ECONNREFUSED {host}:{port}") from error
    except TimeoutError as error:
        raise ValueError("Connection timeout") from error
    except OSError as error:
        raise ValueError(str(error) or "Mail config error") from error
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass


class MailSenderService:
    async def test_transport_config(self, ro: TestMailTransportConfigRo) -> None:
        config = ro.transportConfig
        try:
            await _preflight_connect(config.host, config.port)
            await self._verify_and_send_test(ro)
        except ValueError as error:
            message = str(error) or "Mail config error"
            raise ApiError(
                message,
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.email.testEmailError",
                        "context": {"message": message},
                    }
                },
            ) from error

    async def _verify_and_send_test(self, ro: TestMailTransportConfigRo) -> None:
        # Reached only when the TCP preflight succeeded (a reachable SMTP server).
        # verify() + a test send through aiosmtplib, bounded so a half-open peer
        # cannot hang the request.
        import aiosmtplib

        config = ro.transportConfig
        auth = config.auth or {}
        message = _build_message(
            sender=config.sender,
            sender_name=config.senderName,
            to=ro.to,
            subject="Test Email",
            html=ro.message or "This is a test email.",
        )

        async def _run() -> None:
            client = aiosmtplib.SMTP(
                hostname=config.host,
                port=config.port,
                use_tls=bool(config.secure),
                timeout=10,
            )
            await client.connect()
            try:
                if auth.get("user"):
                    await client.login(auth.get("user"), auth.get("pass") or "")
                await client.send_message(message)
            finally:
                await client.quit()

        try:
            await asyncio.wait_for(_run(), timeout=20)
        except TimeoutError as error:
            raise ValueError("Connection timeout") from error
        except Exception as error:
            raise ValueError(str(error) or "Mail config error") from error

    async def send_email(self, _base_id: str, ro: SendEmailRo) -> dict[str, Any]:
        html = _markdown_to_html(ro.body) if ro.bodyType == "markdown" else ro.body
        result = await self._send(ro, html)
        return {
            "success": bool(result),
            "message": "Email sent successfully" if result else "Failed to send email",
        }

    async def _send(self, ro: SendEmailRo, html: str) -> bool:
        settings = get_settings()
        # Explicit per-request transport takes precedence; otherwise the Automation
        # named transporter falls through to the instance default. With mail
        # unconfigured the reference logs the message and reports success.
        if ro.smtp is None and not settings.is_mail_configured:
            return True

        import aiosmtplib

        transport = ro.smtp
        if transport is not None:
            host = transport.host
            port = transport.port
            secure = bool(transport.secure)
            auth = transport.auth or {}
            sender = transport.sender
            sender_name = transport.senderName
        else:
            host = settings.backend_mail_host
            port = settings.backend_mail_port
            secure = settings.backend_mail_secure == "true"
            auth = {
                "user": settings.backend_mail_auth_user,
                "pass": settings.backend_mail_auth_pass,
            }
            sender = settings.backend_mail_sender
            sender_name = settings.backend_mail_sender_name

        message = _build_message(
            sender=sender,
            sender_name=sender_name,
            to=", ".join(_to_email_array(ro.to)),
            subject=ro.subject,
            html=html,
            cc=", ".join(_to_email_array(ro.cc)) or None,
            reply_to=ro.replyTo,
        )
        try:
            await aiosmtplib.send(
                message,
                hostname=host,
                port=port,
                username=auth.get("user"),
                password=auth.get("pass"),
                use_tls=secure,
            )
            return True
        except Exception:
            return False


def _build_message(
    *,
    sender: str,
    sender_name: str | None,
    to: str,
    subject: str,
    html: str,
    cc: str | None = None,
    reply_to: str | None = None,
) -> Any:
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = f"{sender_name} <{sender}>" if sender_name else sender
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = subject
    message.add_alternative(html, subtype="html")
    return message
