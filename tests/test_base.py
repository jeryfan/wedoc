"""Unit tests for the base module helpers and request schemas."""

import pytest

from wedoc.core.errors import ApiError
from wedoc.core.validation import zod_validate
from wedoc.db.provider import create_schema_sql, drop_schema_sql
from wedoc.modules.base.schemas import (
    AddBaseCollaboratorsBody,
    CreateBaseBody,
    ListBaseCollaboratorQuery,
    UpdateBaseBody,
    UpdateOrderBody,
)
from wedoc.modules.base.service import _PERMISSION_ACTIONS


class TestProviderSql:
    def test_create_schema_quotes_identifier(self):
        assert create_schema_sql("bseTest") == [
            'CREATE SCHEMA IF NOT EXISTS "bseTest"',
            'REVOKE ALL ON SCHEMA "bseTest" FROM PUBLIC',
        ]

    def test_create_schema_escapes_quotes(self):
        sql = create_schema_sql('bad"name')[0]
        assert sql == 'CREATE SCHEMA IF NOT EXISTS "bad""name"'

    def test_create_schema_rejects_null_byte(self):
        with pytest.raises(ValueError):
            create_schema_sql("bad\x00name")

    def test_drop_schema_cascade(self):
        assert drop_schema_sql("bseTest") == 'DROP SCHEMA IF EXISTS "bseTest" CASCADE'


class TestCreateBaseBody:
    def test_minimal(self):
        body = zod_validate(CreateBaseBody, {"spaceId": "spcX"})
        assert body.spaceId == "spcX"
        assert body.name is None
        assert body.icon is None

    def test_space_id_required(self):
        with pytest.raises(ApiError):
            zod_validate(CreateBaseBody, {"name": "B"})


class TestUpdateBaseBody:
    def test_icon_nullable(self):
        body = zod_validate(UpdateBaseBody, {"name": "B2", "icon": None})
        assert body.name == "B2"
        assert body.icon is None

    def test_empty(self):
        body = zod_validate(UpdateBaseBody, {})
        assert body.name is None and body.icon is None


class TestUpdateOrderBody:
    def test_valid_positions(self):
        body = zod_validate(UpdateOrderBody, {"anchorId": "bseA", "position": "before"})
        assert body.position == "before"
        body = zod_validate(UpdateOrderBody, {"anchorId": "bseA", "position": "after"})
        assert body.position == "after"

    def test_invalid_position_message(self):
        with pytest.raises(ApiError) as exc:
            zod_validate(UpdateOrderBody, {"anchorId": "bseA", "position": "middle"})
        assert 'expected one of "before"|"after"' in str(exc.value)

    def test_anchor_required(self):
        with pytest.raises(ApiError):
            zod_validate(UpdateOrderBody, {"position": "before"})


class TestAddBaseCollaboratorsBody:
    def test_owner_role_rejected(self):
        with pytest.raises(ApiError) as exc:
            zod_validate(
                AddBaseCollaboratorsBody,
                {
                    "collaborators": [{"principalId": "usrA", "principalType": "user"}],
                    "role": "owner",
                },
            )
        assert 'expected one of "creator"|"editor"|"commenter"|"viewer"' in str(exc.value)

    def test_creator_role_accepted(self):
        body = zod_validate(
            AddBaseCollaboratorsBody,
            {
                "collaborators": [{"principalId": "usrA", "principalType": "user"}],
                "role": "creator",
            },
        )
        assert body.role == "creator"


class TestListBaseCollaboratorQuery:
    def test_defaults(self):
        query = zod_validate(ListBaseCollaboratorQuery, {})
        assert query.skip is None and query.take is None and query.role is None

    def test_role_list(self):
        query = zod_validate(ListBaseCollaboratorQuery, {"role": ["editor", "viewer"]})
        assert query.role == ["editor", "viewer"]

    def test_coerce_paging(self):
        query = zod_validate(ListBaseCollaboratorQuery, {"skip": "10", "take": "20"})
        assert query.skip == 10 and query.take == 20

    def test_include_system_coerced_like_zod(self):
        query = zod_validate(ListBaseCollaboratorQuery, {"includeSystem": "false"})
        # z.coerce.boolean() on a non-empty string is true.
        assert query.includeSystem is True


class TestPermissionActions:
    def test_prefix_coverage(self):
        for action in _PERMISSION_ACTIONS:
            prefix = action.split("|")[0]
            assert prefix in {"table", "base", "automation", "app", "table_record_history"}

    def test_no_duplicates(self):
        assert len(set(_PERMISSION_ACTIONS)) == len(_PERMISSION_ACTIONS)
