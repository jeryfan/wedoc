"""Body-parser parity: JSON strict/parse errors, urlencoded form bodies, limits."""

from typing import Any

import httpx
import pytest
from conftest import signup

from wedoc.core import validation
from wedoc.core.cache import get_cache


async def _login(client: httpx.AsyncClient):
    await signup(client)


def _body(message: str) -> dict[str, Any]:
    return {"message": message, "status": 400, "code": "validation_error"}


def _schema_error(message: str) -> dict[str, Any]:
    return _body(f"Validation error: {message}")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("params=x", 'Invalid input: expected record, received string at "params"'),
        ("params=", 'Invalid input: expected record, received string at "params"'),
        (
            "params=a&params=b",
            'Invalid input: expected record, received array at "params"',
        ),
        (
            "params[]=1&params[]=2",
            'Invalid input: expected record, received array at "params"',
        ),
        (
            "unknown=1",
            'Invalid input: expected record, received undefined at "params"',
        ),
        (
            "params[constructor]=1",
            'Invalid input: expected record, received object at "params"',
        ),
    ],
)
async def test_urlencoded_bodies_reach_the_schema(
    client: httpx.AsyncClient, body: str, message: str
) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params",
        content=body.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400, response.text
    assert response.json() == _schema_error(message)


@pytest.mark.parametrize(
    "body",
    [
        "params[a]=1&params[b][c]=2",
        "params[__proto__][x]=1",
        "params[a]=%5B%5D",
        "params[a]=1&params=2",
    ],
)
async def test_nested_urlencoded_bodies_are_stored(
    client: httpx.AsyncClient, body: str
) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params",
        content=body.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 201, response.text
    query_id = response.json()["queryId"]
    stored = await get_cache().get(f"query-params:{query_id}")
    assert isinstance(stored, (dict, list))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("null", 'Unexpected token \'n\', "null" is not valid JSON'),
        ('"x"', 'Unexpected token \'"\', ""x"" is not valid JSON'),
        ("7", 'Unexpected token \'7\', "7" is not valid JSON'),
        ("true", 'Unexpected token \'t\', "true" is not valid JSON'),
    ],
)
async def test_json_strict_check_rejects_non_containers(
    client: httpx.AsyncClient, body: str, message: str
) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params", content=body.encode(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400, response.text
    assert response.json() == _body(message)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("{oops", "Expected property name or '}' in JSON at position 1 (line 1 column 2)"),
        ("[1,", "Unexpected end of JSON input"),
        (
            '{"a":1,}',
            "Expected double-quoted property name in JSON at position 7 (line 1 column 8)",
        ),
        (
            "[1 2]",
            "Expected ',' or ']' after array element in JSON at position 3 (line 1 column 4)",
        ),
        ('{"a" 1}', "Expected ':' after property name in JSON at position 5 (line 1 column 6)"),
        (
            '{"a":1 "b":2}',
            "Expected ',' or '}' after property value in JSON at position 7 (line 1 column 8)",
        ),
        ('{"a":01}', "Unexpected number in JSON at position 6 (line 1 column 7)"),
        ('{"a":"\\q"}', "Bad escaped character in JSON at position 7 (line 1 column 8)"),
        ('{"a":"\\u00"}', "Bad Unicode escape in JSON at position 10 (line 1 column 11)"),
        (
            '{\n  "a": 1,\n}',
            "Expected double-quoted property name in JSON at position 12 (line 3 column 1)",
        ),
    ],
)
async def test_json_parse_errors_match_node(
    client: httpx.AsyncClient, body: str, message: str
) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params", content=body.encode(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400, response.text
    assert response.json()["message"] == message


@pytest.mark.parametrize(
    "body",
    ['{"params":{"a":1}}\n', "\ufeff" + '{"params":{"a":1}}', '  {"params":{"a":1}}  '],
)
async def test_json_bodies_with_bom_and_trailing_space_are_accepted(
    client: httpx.AsyncClient, body: str
) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params", content=body.encode(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("content_type", ["text/plain", "application/vnd.api+json", None])
async def test_unmatched_content_types_are_not_parsed(
    client: httpx.AsyncClient, content_type: str | None
) -> None:
    await _login(client)
    headers = {"Content-Type": content_type} if content_type else {}
    response = await client.post(
        "/api/query-params", content=b'{"params":{"a":1}}', headers=headers
    )
    assert response.status_code == 400, response.text
    assert response.json() == _schema_error(
        'Invalid input: expected record, received undefined at "params"'
    )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json; charset=gbk",
        "application/x-www-form-urlencoded; charset=iso-8859-1",
    ],
)
async def test_unsupported_charsets_are_500(
    client: httpx.AsyncClient, content_type: str
) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params", content=b'{"params":{}}', headers={"Content-Type": content_type}
    )
    assert response.status_code == 500, response.text
    assert response.json() == {
        "message": "Internal Server Error",
        "status": 500,
        "code": "internal_server_error",
    }


async def test_too_many_form_parameters_is_500(client: httpx.AsyncClient) -> None:
    await _login(client)
    body = "&".join(f"a{i}=1" for i in range(1001)).encode()
    response = await client.post(
        "/api/query-params",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 500, response.text


async def test_form_depth_overflow_is_500(client: httpx.AsyncClient) -> None:
    await _login(client)
    response = await client.post(
        "/api/query-params",
        content=("a" + "[b]" * 33 + "=1").encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 500, response.text


async def test_body_over_the_size_limit_is_500(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _login(client)
    monkeypatch.setattr(validation, "MAX_BODY_BYTES", 32)
    response = await client.post(
        "/api/query-params",
        content=b'{"params":{"a":"' + b"x" * 64 + b'"}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 500, response.text
