"""Dynamic auth for signature/notify — ports attachments/guard/auth.guard.ts.

A ``Tea-Share-Id`` header routes the request through the shared-view auth path
(``ShareAuthGuard``) instead of the plain chain, so an anonymous visitor of a
public shared form can obtain an upload signature and notify. Without the header
the standard auth chain applies (anonymous is rejected).
"""

from typing import Any

from fastapi import Request

from ...core import cls
from ...core.security.auth import AuthGuard, auth_guard
from ...core.security.constants import ANONYMOUS_USER, is_anonymous


async def signature_notify_guard(request: Request) -> dict[str, Any]:
    share_id = request.headers.get("tea-share-id")
    if not share_id:
        return await auth_guard(request)

    from ..share.service import ShareService

    cls.set("shareViewId", share_id)
    share_info = await ShareService().resolve_share_info(
        share_id, request.cookies.get(share_id)
    )
    if (share_info.get("shareMeta") or {}).get("allowEdit"):
        try:
            await AuthGuard().authorize(request)
        except Exception:
            pass
    current_user_id = cls.get("user.id")
    if not current_user_id or is_anonymous(current_user_id):
        cls.set("user", dict(ANONYMOUS_USER))
    return {}
