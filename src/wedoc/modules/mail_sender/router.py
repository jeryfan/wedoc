"""Routes for /api/mail-sender — ports mail-sender-open-api.controller.ts."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import SendEmailRo, TestMailTransportConfigRo, refine_send_email
from .service import MailSenderService

router = APIRouter(
    prefix="/api/mail-sender",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("/test-transport-config", status_code=201)
async def test_transport_config(request: Request) -> Response:
    ro = TestMailTransportConfigRo.zod_validate(await read_json_body(request))
    await MailSenderService().test_transport_config(ro)
    return Response(status_code=201)


@router.post("/{baseId}/send", status_code=201)
@permissions("base|update")
async def send_email(baseId: str, request: Request) -> dict[str, Any]:
    ro = SendEmailRo.zod_validate(await read_json_body(request))
    refine_send_email(ro)
    return await MailSenderService().send_email(baseId, ro)
