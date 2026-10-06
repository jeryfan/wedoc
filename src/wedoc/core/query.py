"""qs-compatible reads for array query params.

The reference backend parses query strings with ``qs``; the web client (axios)
serializes array query params in bracket notation ``key[]=a&key[]=b``. Starlette's
``QueryParams.getlist(key)`` matches only the plain key, so bracketed arrays are
silently dropped. These helpers read the bracket key first and fall back to the
plain repeated key, reproducing qs array parsing.

``qs`` array semantics for a param ``key``:

* ``key[]=a``            -> ``['a']`` (array, even one element)
* ``key[]=a&key[]=b``    -> ``['a', 'b']``
* ``key=a&key=b``        -> ``['a', 'b']`` (repeated plain key)
* ``key=a``              -> ``'a'`` (scalar): rejected by a schema expecting an array
* absent                 -> undefined
"""

from typing import Any

from .errors import ApiError, HttpErrorCode


def query_list(params: Any, key: str) -> list[str]:
    """Read an array query param, bracket key first then the plain repeated key.

    A lone ``key=a`` yields ``['a']``, matching a zod schema that coerces a single
    string into a one-element array (``z.union([z.string(), z.string().array()])``).
    An absent key yields ``[]``.
    """
    return list(params.getlist(f"{key}[]") or params.getlist(key))


def query_array(
    params: Any, key: str, *, expected: str = "array", required: bool = False
) -> list[str] | None:
    """Read an array query param for a strict schema that rejects a lone scalar.

    Mirrors ``qs`` feeding a bare ``z.array()`` / ``z.tuple()``: the bracket form
    is an array even with one element, a repeated plain key is an array, a single
    plain ``key=a`` is a scalar the schema rejects, and an absent key is undefined
    (rejected only when ``required``). ``expected`` is the zod noun rendered in the
    invalid-type message (``array`` or ``tuple``).
    """
    bracket = params.getlist(f"{key}[]")
    if bracket:
        return list(bracket)
    plain = params.getlist(key)
    if len(plain) >= 2:
        return list(plain)
    if len(plain) == 1:
        raise ApiError(
            f'Validation error: Invalid input: expected {expected}, received string at "{key}"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    if required:
        raise ApiError(
            f'Validation error: Invalid input: expected {expected}, received undefined at "{key}"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return None
