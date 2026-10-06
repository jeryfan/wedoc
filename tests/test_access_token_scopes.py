"""Access-token scope catalog includes the routine actions (matching the
reference's ActionPrefix order: automation, routine, app)."""

from wedoc.core.security.permissions import ALL_ACTIONS
from wedoc.core.validation import zod_validate
from wedoc.modules.access_token.schemas import CreateAccessTokenRo


def test_routine_actions_present_after_automation():
    actions = list(ALL_ACTIONS)
    for scope in ("routine|create", "routine|delete", "routine|read", "routine|update"):
        assert scope in actions
    # routine sits between automation and app in the enum order.
    assert actions.index("automation|update") < actions.index("routine|create")
    assert actions.index("routine|update") < actions.index("app|create")


def test_create_token_accepts_routine_scope():
    body = zod_validate(
        CreateAccessTokenRo,
        {"name": "t", "scopes": ["routine|read"], "expiredTime": "2099-01-01"},
    )
    assert body.scopes == ["routine|read"]
