"""Social login routes (github/google/oidc), registered only when the provider
is listed in SOCIAL_AUTH_PROVIDERS — mirroring the upstream ConditionalModule
gating. With no providers configured these paths fall through to the standard
404 (``Cannot GET /api/auth/github``), which is the deployed baseline contract.

The provider dance itself (authorize redirect, code exchange, account linking)
is not implemented yet; configured-mode requests fail loudly instead of
silently misbehaving.
"""

from fastapi import APIRouter

from ...config import Settings


def social_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    for provider in settings.social_providers:
        if provider not in ("github", "google", "oidc"):
            continue

        async def not_implemented() -> None:
            raise RuntimeError("social auth is not implemented")

        router.add_api_route(
            f"/{provider}", not_implemented, methods=["GET"], status_code=200
        )
        router.add_api_route(
            f"/{provider}/callback", not_implemented, methods=["GET"], status_code=200
        )
    return router
