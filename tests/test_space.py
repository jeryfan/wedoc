"""Unit tests for the space module helpers and request schemas."""

import pytest

from wedoc.core.errors import ApiError
from wedoc.core.validation import zod_validate
from wedoc.modules.space.schemas import (
    AddCollaboratorsBody,
    BaseEntryMapQuery,
    CreateSpaceBody,
    ListCollaboratorQuery,
    SpaceSearchQuery,
)
from wedoc.modules.space.service import get_uniq_name


class TestGetUniqName:
    def test_no_conflict_returns_name(self):
        assert get_uniq_name("Space", []) == "Space"
        assert get_uniq_name("Space", ["Other"]) == "Space"

    def test_appends_number(self):
        assert get_uniq_name("Space", ["Space"]) == "Space 2"
        assert get_uniq_name("Space", ["Space", "Space 2"]) == "Space 3"

    def test_existing_suffix_strips_and_increments(self):
        assert get_uniq_name("Space 2", ["Space 2"]) == "Space 3"
        assert get_uniq_name("Space 3", ["Space 3", "Space 4"]) == "Space 5"

    def test_numeric_name_is_not_split(self):
        assert get_uniq_name("2026", ["2026"]) == "2026 2"


class TestCreateSpaceBody:
    def test_name_optional(self):
        body = zod_validate(CreateSpaceBody, {})
        assert body.name is None

    def test_name_bounds(self):
        with pytest.raises(ApiError) as exc:
            zod_validate(CreateSpaceBody, {"name": "x" * 101})
        assert "<=100 characters" in exc.value.message
        with pytest.raises(ApiError):
            zod_validate(CreateSpaceBody, {"name": ""})


class TestBaseEntryMapQuery:
    def test_take_parsed_from_string(self):
        query = zod_validate(BaseEntryMapQuery, {"take": "5"})
        assert query.take == 5

    def test_take_rejects_non_positive(self):
        with pytest.raises(ApiError):
            zod_validate(BaseEntryMapQuery, {"take": "0"})


class TestSpaceSearchQuery:
    def test_defaults(self):
        query = zod_validate(SpaceSearchQuery, {"search": "x"})
        assert query.pageSize == 10

    def test_page_size_bounds(self):
        with pytest.raises(ApiError) as exc:
            zod_validate(SpaceSearchQuery, {"search": "x", "pageSize": "51"})
        assert "<=50" in exc.value.message


class TestListCollaboratorQuery:
    def test_coerce_boolean_truthy_string(self):
        # z.coerce.boolean() on a query string: any non-empty string is true
        query = zod_validate(ListCollaboratorQuery, {"includeBase": "false"})
        assert query.includeBase is True
        query = zod_validate(ListCollaboratorQuery, {"includeSystem": ""})
        assert query.includeSystem is False

    def test_principal_type_enum(self):
        with pytest.raises(ApiError) as exc:
            zod_validate(ListCollaboratorQuery, {"type": "alien"})
        assert 'Invalid option: expected one of "user"|"User"' in exc.value.message


class TestAddCollaboratorsBody:
    def test_role_enum(self):
        body = zod_validate(
            AddCollaboratorsBody,
            {
                "collaborators": [{"principalId": "usrX", "principalType": "user"}],
                "role": "editor",
            },
        )
        assert body.role == "editor"
        with pytest.raises(ApiError):
            zod_validate(
                AddCollaboratorsBody,
                {
                    "collaborators": [{"principalId": "usrX", "principalType": "user"}],
                    "role": "superadmin",
                },
            )
