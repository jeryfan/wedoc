"""Routes for /api/template — ports template-open-api.controller.ts.

Literal-first-segment routes are registered before the /{templateId} catch-alls
so `/published`, `/category/*`, `/by-base/*`, `/unpublish/*`, `/permalink/*`
resolve correctly. Permission gating mirrors the controller decorators.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    CreateTemplateCategoryRo,
    CreateTemplateRo,
    TemplateListQueryRo,
    TemplateQueryRo,
    UpdateOrderRo,
    UpdateTemplateCategoryRo,
    UpdateTemplateRo,
)
from .service import TemplateService

router = APIRouter(prefix="/api/template")

_INSTANCE = [Depends(auth_guard), Depends(permission_guard)]
_AUTH = [Depends(auth_guard)]


def _query(request: Request, keys: tuple[str, ...]) -> dict[str, Any]:
    params = request.query_params
    return {k: params[k] for k in keys if k in params}


# --- literal first-segment routes -----------------------------------------


@router.get("", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def get_template_list(request: Request) -> list[dict[str, Any]]:
    query = TemplateListQueryRo.zod_validate(_query(request, ("skip", "take")))
    return await TemplateService().get_all_template_list(query)


@router.get("/published", status_code=200)
async def get_published_template_list(request: Request) -> list[dict[str, Any]]:
    query = TemplateQueryRo.zod_validate(
        _query(request, ("featured", "categoryId", "skip", "take", "search"))
    )
    return await TemplateService().get_published_template_list(query)


@router.post("/create", status_code=201, dependencies=_INSTANCE)
@permissions("instance|update")
async def create_template(request: Request) -> dict[str, Any]:
    ro = CreateTemplateRo.zod_validate(await read_json_body(request))
    return await TemplateService().create_template(ro)


@router.post("/category/create", status_code=201, dependencies=_INSTANCE)
@permissions("instance|update")
async def create_template_category(request: Request) -> dict[str, Any]:
    ro = CreateTemplateCategoryRo.zod_validate(await read_json_body(request))
    return await TemplateService().create_template_category(ro)


@router.get("/category/list", status_code=200, dependencies=_AUTH)
async def get_template_category_list() -> list[dict[str, Any]]:
    return await TemplateService().get_template_category_list()


@router.delete("/category/{category_id}", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def delete_template_category(category_id: str) -> Response:
    await TemplateService().delete_template_category(category_id)
    return Response(status_code=200)


@router.patch("/category/{category_id}", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def update_template_category(category_id: str, request: Request) -> Response:
    ro = UpdateTemplateCategoryRo.zod_validate(await read_json_body(request))
    await TemplateService().update_template_category(category_id, ro)
    return Response(status_code=200)


@router.put("/category/{category_id}/order", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def update_template_category_order(category_id: str, request: Request) -> Response:
    ro = UpdateOrderRo.zod_validate(await read_json_body(request))
    await TemplateService().update_template_category_order(category_id, ro)
    return Response(status_code=200)


@router.get("/by-base/{base_id}", status_code=200, dependencies=_AUTH)
async def get_template_by_base_id(base_id: str) -> Any:
    result = await TemplateService().get_template_by_base_id(base_id)
    return result if result is not None else Response(status_code=200)


@router.delete("/unpublish/{template_id}", status_code=200, dependencies=_AUTH)
async def unpublish_template(template_id: str) -> dict[str, Any]:
    return await TemplateService().delete_template(template_id)


@router.get("/permalink/{identifier}", status_code=200)
async def get_template_permalink(identifier: str) -> dict[str, Any]:
    return await TemplateService().resolve_permalink(identifier)


# --- {templateId} two-segment routes ---------------------------------------


@router.patch("/{template_id}/pin-top", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def pin_top_template(template_id: str) -> Response:
    await TemplateService().pin_top_template(template_id)
    return Response(status_code=200)


@router.put("/{template_id}/order", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def update_order(template_id: str, request: Request) -> Response:
    ro = UpdateOrderRo.zod_validate(await read_json_body(request))
    await TemplateService().update_order(template_id, ro)
    return Response(status_code=200)


@router.post("/{template_id}/snapshot", status_code=201, dependencies=_INSTANCE)
@permissions("instance|update")
async def create_template_snapshot(template_id: str) -> dict[str, Any]:
    return await TemplateService().create_template_snapshot(template_id)


@router.patch("/{template_id}/visit", status_code=200)
async def increment_template_visit_count(template_id: str) -> Response:
    await TemplateService().increment_template_visit_count(template_id)
    return Response(status_code=200)


# --- {templateId} single-segment routes (registered last) ------------------


@router.delete("/{template_id}", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def delete_template(template_id: str) -> dict[str, Any]:
    return await TemplateService().delete_template(template_id)


@router.patch("/{template_id}", status_code=200, dependencies=_INSTANCE)
@permissions("instance|update")
async def update_template(template_id: str, request: Request) -> Response:
    ro = UpdateTemplateRo.zod_validate(await read_json_body(request))
    await TemplateService().update_template(template_id, ro)
    return Response(status_code=200)


@router.get("/{template_id}", status_code=200)
async def get_template_by_id(template_id: str) -> dict[str, Any]:
    return await TemplateService().get_template_detail_by_id(template_id)
