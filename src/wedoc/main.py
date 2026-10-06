"""ASGI application assembly."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .core.errors import ApiError, HttpErrorCode, default_code_by_status
from .db import engine as db_engine
from .db.migrator import migrate_all

logger = structlog.get_logger(__name__)


def _error_body(message: str, status: int, code: str, data: dict | None = None) -> dict:
    body: dict = {"message": message, "status": status, "code": code}
    if data is not None:
        body["data"] = data
    return body


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=_error_body(exc.message, exc.status, str(exc.code), exc.data),
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404 and exc.detail == "Not Found":
        # Express `Cannot <METHOD> <originalUrl>` includes the raw query string.
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        message = f"Cannot {request.method} {target}"
    else:
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    code = default_code_by_status(exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(message, exc.status_code, str(code)),
    )


async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    from .core.validation import format_zod_errors

    message = format_zod_errors(None, exc.errors())
    return JSONResponse(
        status_code=400,
        content=_error_body(message, 400, str(HttpErrorCode.VALIDATION_ERROR)),
    )


def _format_validation_errors(errors: list[dict]) -> str:
    """Kept for backwards import compatibility; the handler delegates to
    core.validation.format_zod_errors."""
    from .core.validation import format_zod_errors

    return format_zod_errors(None, errors)


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    settings = get_settings()
    if settings.node_env == "test":
        message = f"Internal Server Error: {exc}"
    else:
        message = "Internal Server Error"
    logger.exception("unhandled error", error=str(exc))
    return JSONResponse(
        status_code=500,
        content=_error_body(message, 500, str(HttpErrorCode.INTERNAL_SERVER_ERROR)),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await migrate_all(settings.meta_database_dsn)
    await db_engine.init_db()
    from .modules.plugin.builtin_seed import seed_builtin_plugins

    await seed_builtin_plugins()
    app.state.settings = settings
    yield
    from .realtime.pubsub import close_pubsub

    await close_pubsub()
    await db_engine.close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="wedoc",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    from .core.cls import ClsMiddleware
    from .core.security.auth import EnsureLoginRedirect

    app.add_middleware(ClsMiddleware)

    from .core.middleware import SessionCsrfMiddleware

    app.add_middleware(SessionCsrfMiddleware)

    async def ensure_login_redirect_handler(
        _request: Request, exc: EnsureLoginRedirect
    ) -> JSONResponse:
        from starlette.responses import PlainTextResponse

        # Mirror Express res.redirect(): 302 + "Found. Redirecting to <url>" body.
        return PlainTextResponse(  # type: ignore[return-value]
            f"Found. Redirecting to {exc.redirect_url}",
            status_code=302,
            headers={"Location": exc.redirect_url},
        )

    app.add_exception_handler(EnsureLoginRedirect, ensure_login_redirect_handler)  # type: ignore[arg-type]

    from .core.middleware import (
        CorsMiddleware,
        JsonCharsetMiddleware,
        RequestContextMiddleware,
        SecurityHeadersMiddleware,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorsMiddleware)
    app.add_middleware(JsonCharsetMiddleware)

    from .modules.auth.router import router as auth_router
    from .modules.auth.social import social_router
    from .modules.base.router import router as base_router
    from .modules.base_node.router import router as base_node_router
    from .modules.field.router import router as field_router
    from .modules.health import router as health_router
    from .modules.invitation.router import router as invitation_router
    from .modules.record.router import router as record_router
    from .modules.space.router import router as space_router
    from .modules.table.router import router as table_router
    from .modules.user.router import router as user_router
    from .modules.view.router import router as view_router

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(user_router)
    app.include_router(space_router)
    app.include_router(base_router)
    app.include_router(base_node_router)
    app.include_router(invitation_router)
    app.include_router(table_router)
    app.include_router(field_router)
    app.include_router(view_router)
    app.include_router(record_router)
    from .modules.aggregation.router import router as aggregation_router

    app.include_router(aggregation_router)
    from .modules.undo_redo.router import router as undo_redo_router
    from .realtime.router import router as realtime_router

    app.include_router(undo_redo_router)
    app.include_router(realtime_router)
    from .modules.notification.router import router as notification_router

    app.include_router(notification_router)
    from .modules.comment.router import router as comment_router

    app.include_router(comment_router)
    from .modules.trash.router import router as trash_router

    app.include_router(trash_router)
    from .modules.base_share.router import open_router as base_share_open_router
    from .modules.base_share.router import router as base_share_router

    app.include_router(base_share_router)
    app.include_router(base_share_open_router)
    from .modules.attachment.router import public_router as attachment_public_router
    from .modules.attachment.router import router as attachment_router

    app.include_router(attachment_router)
    app.include_router(attachment_public_router)
    from .modules.share.router import router as share_router

    app.include_router(share_router)
    from .modules.selection.router import router as selection_router

    app.include_router(selection_router)
    from .modules.export.router import router as export_router

    app.include_router(export_router)
    from .modules.import_.router import router as import_router

    app.include_router(import_router)
    from .modules.third_party_import.router import router as third_party_import_router

    app.include_router(third_party_import_router)
    from .modules.access_token.router import router as access_token_router

    app.include_router(access_token_router)
    from .modules.pin.router import router as pin_router

    app.include_router(pin_router)
    from .modules.short_link.router import router as short_link_router

    app.include_router(short_link_router)
    from .modules.organization.router import router as organization_router

    app.include_router(organization_router)
    from .modules.setting.router import admin_router as setting_admin_router
    from .modules.setting.router import public_router as setting_public_router
    from .modules.setting.router import router as setting_router

    app.include_router(setting_public_router)
    app.include_router(setting_router)
    app.include_router(setting_admin_router)
    from .modules.mail_sender.router import router as mail_sender_router

    app.include_router(mail_sender_router)
    from .modules.template.router import router as template_router

    app.include_router(template_router)
    from .modules.oauth.router import router as oauth_router

    app.include_router(oauth_router)
    from .modules.integrity.router import router as integrity_router

    app.include_router(integrity_router)
    from .modules.ai.router import chat_router
    from .modules.ai.router import router as ai_router

    app.include_router(ai_router)
    app.include_router(chat_router)
    from .modules.dashboard.router import router as dashboard_router

    app.include_router(dashboard_router)
    from .modules.plugin.router import chart_router as plugin_chart_router
    from .modules.plugin.router import router as plugin_router

    app.include_router(plugin_chart_router)
    app.include_router(plugin_router)
    from .modules.plugin_panel.router import router as plugin_panel_router

    app.include_router(plugin_panel_router)
    from .modules.plugin_context_menu.router import router as plugin_context_menu_router

    app.include_router(plugin_context_menu_router)
    from .modules.v2.router import public_router as v2_public_router
    from .modules.v2.router import router as v2_router

    app.include_router(v2_public_router)
    app.include_router(v2_router)
    from .modules.oauth_server.router import auth_router as oauth_server_auth_router
    from .modules.oauth_server.router import ensure_router as oauth_server_ensure_router
    from .modules.oauth_server.router import public_router as oauth_server_public_router

    app.include_router(oauth_server_public_router)
    app.include_router(oauth_server_ensure_router)
    app.include_router(oauth_server_auth_router)
    from .modules.integrity_v2.router import router as integrity_v2_router

    app.include_router(integrity_v2_router)
    social = social_router(settings)
    if social.routes:
        app.include_router(social)
    # frontend_proxy owns the catch-all and MUST be registered last so every
    # /api/* and /socket route is matched before the page-route fallback.
    from .modules.frontend_proxy import router as frontend_proxy_router

    app.include_router(frontend_proxy_router)
    app.state.settings = settings
    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "wedoc.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level,
    )
