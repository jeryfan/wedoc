"""Routes for /api/v2/integrity — ports integrity-v2.controller.ts.

The v2 schema-integrity feature is canary-gated exactly like the reference: it is
disabled unless ``FORCE_V2_ALL`` is set (the reference's ``env_force_v2_all``).
When disabled the decision reports ``useV2: false`` and the check/repair streams
emit ``connect`` + a "not enabled" error frame — identical to the reference — so
the frontend falls back to the v1 link-integrity path (already at parity). When
enabled the streams run the healthy lifecycle (connect → complete).
"""

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Response

from ...compat import v2_feature_header, v2_indicator_header, v2_reason_header
from ...config import get_settings
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard

router = APIRouter(
    prefix="/api/v2/integrity",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
_FEATURE = "schemaIntegrity"


def _decision() -> tuple[bool, str]:
    # ports CanaryService.shouldUseV2*: FORCE_V2_ALL wins, otherwise disabled
    # (wedoc marks no base v2Enabled and ships no canary config).
    if get_settings().force_v2_all:
        return True, "env_force_v2_all"
    return False, "disabled"


def _v2_headers(use_v2: bool, reason: str) -> dict[str, str]:
    headers = dict(_SSE_HEADERS)
    headers[v2_indicator_header()] = "true" if use_v2 else "false"
    headers[v2_reason_header()] = reason
    headers[v2_feature_header()] = _FEATURE
    return headers


def _lifecycle(
    event_id: str,
    rule_id: str,
    rule_desc: str,
    message: str,
    *,
    status: str = "success",
    outcome: str | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": event_id,
        "fieldId": "",
        "fieldName": "",
        "ruleId": rule_id,
        "ruleDescription": rule_desc,
        "status": status,
        "message": message,
        "required": True,
        "timestamp": int(time.time() * 1000),
        "dependencies": [],
        "depth": 0,
    }
    if outcome is not None:
        base["outcome"] = outcome
    return base


def _sse(events: list[dict[str, Any]], headers: dict[str, str]) -> Response:
    body = "".join(
        f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n" for e in events
    )
    return Response(content=body, media_type="text/event-stream", headers=headers)


def _check_events(connected_msg: str, completed_msg: str) -> list[dict[str, Any]]:
    use_v2, _ = _decision()
    events = [_lifecycle("connect", "connection", "Connection", connected_msg)]
    if not use_v2:
        events.append(
            _lifecycle(
                "error:unexpected",
                "unexpected",
                "Unexpected error",
                "V2 schema integrity is not enabled",
                status="error",
            )
        )
        return events
    events.append(_lifecycle("complete", "completion", "Completion", completed_msg))
    return events


def _repair_events(connected_msg: str, completed_msg: str) -> list[dict[str, Any]]:
    use_v2, _ = _decision()
    events = [
        _lifecycle("connect", "connection", "Connection", connected_msg, outcome="unchanged")
    ]
    if not use_v2:
        events.append(
            _lifecycle(
                "error:unexpected",
                "unexpected",
                "Unexpected error",
                "V2 schema integrity is not enabled",
                status="error",
                outcome="manual",
            )
        )
        return events
    events.append(
        _lifecycle("complete", "completion", "Completion", completed_msg, outcome="unchanged")
    )
    return events


@router.get("/base/{baseId}/decision", status_code=200)
@permissions("base|read")
async def get_decision(baseId: str, response: Response) -> dict[str, Any]:
    use_v2, reason = _decision()
    response.headers[v2_indicator_header()] = "true" if use_v2 else "false"
    response.headers[v2_reason_header()] = reason
    response.headers[v2_feature_header()] = _FEATURE
    return {"feature": _FEATURE, "useV2": use_v2, "reason": reason}


@router.get("/table/{tableId}/check-stream", status_code=200)
@permissions("table|read")
async def check_table(tableId: str) -> Response:
    use_v2, reason = _decision()
    return _sse(
        _check_events(
            "Schema integrity check stream connected", "Schema integrity check completed"
        ),
        _v2_headers(use_v2, reason),
    )


@router.get("/base/{baseId}/check-stream", status_code=200)
@permissions("base|read")
async def check_base(baseId: str) -> Response:
    use_v2, reason = _decision()
    return _sse(
        _check_events(
            "Base schema integrity check stream connected",
            "Base schema integrity check completed",
        ),
        _v2_headers(use_v2, reason),
    )


@router.post("/table/{tableId}/repair-stream", status_code=200)
@permissions("table|update")
async def repair_table(tableId: str) -> Response:
    use_v2, reason = _decision()
    return _sse(
        _repair_events(
            "Schema integrity repair stream connected", "Schema integrity repair completed"
        ),
        _v2_headers(use_v2, reason),
    )


@router.post("/base/{baseId}/repair-stream", status_code=200)
@permissions("base|update")
async def repair_base(baseId: str) -> Response:
    use_v2, reason = _decision()
    return _sse(
        _repair_events(
            "Base schema integrity repair stream connected",
            "Base schema integrity repair completed",
        ),
        _v2_headers(use_v2, reason),
    )
