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
        message = f"Cannot {request.method} {request.url.path}"
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
    app.state.settings = settings
    yield
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
        from fastapi.responses import RedirectResponse

        return RedirectResponse(exc.redirect_url, status_code=302)  # type: ignore[return-value]

    app.add_exception_handler(EnsureLoginRedirect, ensure_login_redirect_handler)  # type: ignore[arg-type]

    from .core.middleware import (
        CorsMiddleware,
        RequestContextMiddleware,
        SecurityHeadersMiddleware,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorsMiddleware)

    from .modules.auth.router import router as auth_router
    from .modules.auth.social import social_router
    from .modules.base.router import router as base_router
    from .modules.base_node.router import router as base_node_router
    from .modules.field.router import router as field_router
    from .modules.health import router as health_router
    from .modules.invitation.router import router as invitation_router
    from .modules.space.router import router as space_router
    from .modules.table.router import router as table_router
    from .modules.user.router import router as user_router

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(user_router)
    app.include_router(space_router)
    app.include_router(base_router)
    app.include_router(base_node_router)
    app.include_router(invitation_router)
    app.include_router(table_router)
    app.include_router(field_router)
    social = social_router(settings)
    if social.routes:
        app.include_router(social)
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
