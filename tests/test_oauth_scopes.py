"""OAuth client scope-enum parity.

scopes is z.array(z.enum(OAUTH_ACTIONS)): a bad element reports its index
(scopes[0]), and the EE action set includes the routine|* actions.
"""

import pytest

from wedoc.core.errors import ApiError
from wedoc.core.validation import zod_validate
from wedoc.modules.oauth.schemas import OAUTH_ACTIONS, OAuthCreateRo

_VALID = {"name": "App", "homepage": "https://ex.com", "redirectUris": ["https://ex.com/cb"]}


def test_routine_actions_present():
    for a in ("routine|create", "routine|delete", "routine|read", "routine|update"):
        assert a in OAUTH_ACTIONS


def test_bad_scope_reports_element_index():
    with pytest.raises(ApiError) as exc:
        zod_validate(OAuthCreateRo, {**_VALID, "scopes": ["bogus|x"]})
    msg = exc.value.message
    assert msg.endswith('|"user|integrations" at "scopes[0]"')
    assert '"routine|create"' in msg


def test_valid_scopes_dedup_preserves_order():
    ro = zod_validate(
        OAuthCreateRo, {**_VALID, "scopes": ["base|read", "base|read", "record|create"]}
    )
    assert ro.scopes == ["base|read", "record|create"]
