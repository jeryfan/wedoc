"""zod semantics Pydantic does not reproduce: UTF-16 length, nested nulls, issue order."""

import httpx
import pytest
from conftest import signup, uniq_email


def _validation(message: str) -> dict:
    return {"message": f"Validation error: {message}", "status": 400, "code": "validation_error"}


@pytest.mark.parametrize(
    ("password", "status"),
    [
        # JS length counts UTF-16 code units, so 8 units pass even with emoji.
        ("a1😀😀😀", 201),
        ("😀" * 4, 400),
        ("a1" + "😀" * 3, 201),
        ("a1" + "😀" * 2, 400),
    ],
)
async def test_password_length_is_measured_in_utf16_code_units(
    client: httpx.AsyncClient, password: str, status: int
) -> None:
    response = await client.post(
        "/api/auth/signup", json={"email": uniq_email(), "password": password}
    )
    assert response.status_code == status, response.text


async def test_strength_regex_runs_over_code_units(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup", json={"email": uniq_email(), "password": "😀" * 4}
    )
    assert response.json()["message"] == (
        "Validation error: Must contain at least one letter and one number at \"password\""
    )


async def test_default_space_name_maximum_is_utf16_based(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"email": uniq_email(), "password": "Passw0rd1", "defaultSpaceName": "😀" * 51},
    )
    assert response.status_code == 400, response.text
    assert response.json() == _validation(
        'Too big: expected string to have <=100 characters at "defaultSpaceName"'
    )


@pytest.mark.parametrize("field", ["query", "referer"])
async def test_nested_optional_null_is_rejected(client: httpx.AsyncClient, field: str) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"email": uniq_email(), "password": "Passw0rd1", "refMeta": {field: None}},
    )
    assert response.status_code == 400, response.text
    assert response.json() == _validation(
        f'Invalid input: expected string, received null at "refMeta.{field}"'
    )


async def test_explicit_null_on_optional_top_level_field_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"email": uniq_email(), "password": "Passw0rd1", "turnstileToken": None},
    )
    assert response.status_code == 400, response.text
    assert response.json() == _validation(
        'Invalid input: expected string, received null at "turnstileToken"'
    )


async def test_wrong_typed_string_still_reports_length_bounds(client: httpx.AsyncClient) -> None:
    await signup(client)
    response = await client.patch(
        "/api/auth/change-password", json={"password": [], "newPassword": "Passw0rd2"}
    )
    assert response.status_code == 400, response.text
    assert response.json() == _validation(
        'Invalid input: expected string, received array at "password"; '
        "Too small: expected array to have >=8 items at \"password\""
    )


async def test_issues_are_collected_in_schema_order(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup", json={"password": "bad", "turnstileToken": None}
    )
    assert response.status_code == 400, response.text
    assert response.json() == _validation(
        'Invalid input: expected string, received undefined at "email"; '
        'Too small: expected string to have >=8 characters at "password"; '
        'Must contain at least one letter and one number at "password"; '
        'Invalid input: expected string, received null at "turnstileToken"'
    )
