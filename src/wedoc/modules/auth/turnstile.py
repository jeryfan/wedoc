"""Authentication challenge verification and transient-failure retries."""

import asyncio
from typing import Any

import httpx

from ...config import Settings
from ...core.errors import ApiError, HttpErrorCode


async def validate_turnstile(
    settings: Settings, token: Any, remote_ip: str | None = None
) -> None:
    if not settings.is_turnstile_enabled:
        return
    if token is None or token is False or token == "" or token == 0:
        raise ApiError("Turnstile token is required", HttpErrorCode.VALIDATION_ERROR)
    if (
        not isinstance(token, str)
        or len(token.encode("utf-16-le", errors="surrogatepass")) // 2 > 2048
    ):
        raise ApiError("Invalid verification token", HttpErrorCode.VALIDATION_ERROR)

    payload = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    "https://challenges.cloudflare.com/turnstile/v0/siteverify", json=payload
                )
                if response.is_success:
                    result = response.json()
                    if result is None:
                        raise TypeError("Missing verification result")
                    if isinstance(result, dict) and result.get("success"):
                        return
                    raise ApiError(
                        "Verification failed. Please refresh and try again.",
                        HttpErrorCode.VALIDATION_ERROR,
                    )
            except (httpx.RequestError, ValueError, TypeError):
                pass
            if attempt < 2:
                await asyncio.sleep(2**attempt)
    raise ApiError(
        "Verification service temporarily unavailable. Please try again.",
        HttpErrorCode.VALIDATION_ERROR,
    )
