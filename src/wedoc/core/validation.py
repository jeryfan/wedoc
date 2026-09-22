"""Zod-compatible request validation on top of Pydantic.

The upstream backend validates every request payload with zod 4.x and renders
failures through ``zod-validation-error`` v4: the wire message is

    Validation error: <issue1> at "<path1>"; <issue2> at "<path2>"

where each issue text is zod's own English message, title-cased, and paths use
``a.b[0]`` notation. Schemas in ``wedoc.modules.*.schemas`` are Pydantic models
(field-for-field ports of the zod schemas); this module translates Pydantic
validation failures into the exact zod wording so both backends emit identical
``message`` strings.

Usage in a router::

    body = SignupBody.zod_validate(await read_json_body(request))   # JSON body
    query = DeleteUserQuery.zod_validate(dict(request.query_params))  # query

Custom per-field rules (email format, password strength, enums) are declared in
the schemas with the ``zod_*`` helpers below, which raise the zod message
verbatim.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError

from .errors import ApiError, HttpErrorCode

# zod 4.1.8 regexes.js `email` (the default z.string().email() pattern)
ZOD_EMAIL_RE = re.compile(
    r"^(?!\.)(?!.*\.\.)[A-Za-z0-9_'+\-.]*[A-Za-z0-9_+-]@([A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}$"
)

# packages/openapi auth/types.ts signupPasswordSchema
SIGNUP_PASSWORD_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d).{8,}$", re.IGNORECASE)
SIGNUP_PASSWORD_MESSAGE = "Must contain at least one letter and one number"


def zod_email(value: str) -> str:
    """z.string().email(): reject with zod's message; value passes through unchanged."""
    if not ZOD_EMAIL_RE.match(value):
        raise ValueError("Invalid email address")
    return value


# Item-level email rule: pydantic reports failures with the element index in
# loc, matching zod's `at "list[0]"` path rendering.
ZodEmailStr = Annotated[str, AfterValidator(zod_email)]


@dataclass(frozen=True)
class ZodNullable:
    """Annotated metadata marker: zod `.nullable()` — explicit null is accepted."""


ZodNullableStr = Annotated[str | None, ZodNullable()]


def zod_password_checks(value: str, *, strength: bool) -> str:
    """passwordSchema (min 8) + optional signupPasswordSchema strength regex."""
    messages: list[str] = []
    if len(value) < 8:
        messages.append("Too small: expected string to have >=8 characters")
    if strength and not SIGNUP_PASSWORD_RE.match(value):
        messages.append(SIGNUP_PASSWORD_MESSAGE)
    if messages:
        if len(messages) == 1:
            raise ValueError(messages[0])
        raise ZodMultiError(messages)
    return value


def zod_enum_check(value: Any, options: list[str]) -> str:
    """z.enum over a TS enum object: invalid input lists every option quoted, |-joined."""
    if not isinstance(value, str) or value not in options:
        joined = "|".join(f'"{v}"' for v in options)
        raise ValueError(f"Invalid option: expected one of {joined}")
    return value


@dataclass(frozen=True)
class ZodEnumSpec:
    """Annotated metadata carrying a field's zod-enum options."""

    options: tuple[str, ...]


def ZodEnumStr(options: list[str]) -> Any:
    """Annotated str with zod-enum semantics.

    Present values go through ``zod_enum_check``; absent fields render zod's
    "Invalid option: expected one of ..." instead of the plain missing-string
    message (zod reports a missing enum field as an option mismatch).
    """
    def check(value: Any) -> str:
        return zod_enum_check(value, list(options))

    return Annotated[str, AfterValidator(check), ZodEnumSpec(tuple(options))]


def zod_int(value: Any) -> int:
    """z.number().int(): base-type failure says 'number', non-integer says 'int'.

    Booleans are rejected (JS typeof true === 'boolean'), integral floats accepted.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _ZodTypeError("number", value)
    if isinstance(value, float) and not value.is_integer():
        raise _ZodTypeError("int", value)
    return int(value)


async def read_json_body(request: Any) -> Any:
    """Express body-parser json() equivalent: {} for absent/non-JSON bodies."""
    content_type = request.headers.get("content-type") or ""
    if "application/json" not in content_type:
        return {}
    raw = await request.body()
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(
            f"Unexpected token in JSON at position {exc.pos}",
            HttpErrorCode.VALIDATION_ERROR,
        ) from exc


class _ZodTypeError(ValueError):
    """Carries the zod invalid_type expected/received pair through Pydantic."""

    def __init__(self, expected: str, input_value: Any) -> None:
        super().__init__(f"expected {expected}")
        self.expected = expected
        self.input_value = input_value


class ZodMultiError(ValueError):
    """One field failing several zod checks (zod collects all issues per field)."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


def _parsed_type(value: Any) -> str:
    """zod v4 en locale parsedType()."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "NaN" if isinstance(value, float) and value != value else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _join_path(loc: tuple[Any, ...]) -> str:
    """zod-validation-error joinPath(): dotted identifiers, [i] for indexes."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
            continue
        part = str(part)
        if '"' in part:
            out += f'["{part.replace(chr(34), chr(92) + chr(34))}"]'
        elif not re.match(r"^[$_a-zA-Z][$\w]*$", part):
            out += f'["{part}"]'
        else:
            out += ("." if out else "") + part
    return out


def _field_expected(model: type[BaseModel] | None, loc: tuple[Any, ...]) -> str | None:
    """The zod 'expected' noun for a field path: string/number/boolean/array/object."""
    node: Any = model
    for part in loc:
        if node is None or not isinstance(part, str):
            return None
        field = getattr(node, "model_fields", {}).get(part)
        if field is None:
            return None
        node = field.annotation
        # unwrap Optional[...] / unions
        args = getattr(node, "__args__", None)
        if args:
            node = next((a for a in args if a is not type(None)), node)
    if node is None:
        return None
    if isinstance(node, type):
        if issubclass(node, str):
            return "string"
        if issubclass(node, bool):
            return "boolean"
        if issubclass(node, int | float):
            return "number"
        if issubclass(node, BaseModel) or issubclass(node, dict):
            return "object"
        if issubclass(node, list):
            return "array"
    origin = getattr(node, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return None


def _field_enum_spec(model: type[BaseModel] | None, loc: tuple[Any, ...]) -> ZodEnumSpec | None:
    """The ZodEnumSpec for a field path, if the field is a zod-enum string."""
    node: Any = model
    for part in loc:
        if node is None or not isinstance(part, str):
            return None
        field = getattr(node, "model_fields", {}).get(part)
        if field is None:
            return None
        for meta in field.metadata:
            if isinstance(meta, ZodEnumSpec):
                return meta
        node = field.annotation
        args = getattr(node, "__args__", None)
        if args:
            node = next((a for a in args if a is not type(None)), node)
    return None


def _translate(
    model: type[BaseModel] | None, err: dict[str, Any]
) -> list[tuple[str, tuple[Any, ...]]]:
    """One Pydantic error dict -> [(zod message, zod path)]."""
    err_type: str = err.get("type", "")
    ctx: dict[str, Any] = err.get("ctx") or {}
    loc = tuple(p for p in err.get("loc", ()) if p not in ("body", "query", "params"))
    input_value = err.get("input")

    def one(message: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [(message, loc)]

    if err_type == "missing":
        spec = _field_enum_spec(model, loc)
        if spec is not None:
            joined = "|".join(f'"{v}"' for v in spec.options)
            return one(f"Invalid option: expected one of {joined}")
        expected = _field_expected(model, loc) or "undefined"
        return one(f"Invalid input: expected {expected}, received undefined")

    if err_type == "string_type":
        return one(f"Invalid input: expected string, received {_parsed_type(input_value)}")
    if err_type in ("int_type", "float_type"):
        return one(f"Invalid input: expected number, received {_parsed_type(input_value)}")
    if err_type == "bool_type":
        return one(f"Invalid input: expected boolean, received {_parsed_type(input_value)}")
    if err_type in ("list_type", "array_type"):
        return one(f"Invalid input: expected array, received {_parsed_type(input_value)}")
    if err_type in ("dict_type", "model_type", "model_attributes_type"):
        return one(f"Invalid input: expected object, received {_parsed_type(input_value)}")

    if err_type == "string_too_short":
        minimum = ctx.get("min_length", 0)
        return one(f"Too small: expected string to have >={minimum} characters")
    if err_type == "string_too_long":
        maximum = ctx.get("max_length", 0)
        return one(f"Too big: expected string to have <={maximum} characters")
    if err_type == "greater_than_equal":
        return one(f"Too small: expected number to be >={ctx.get('ge')}")
    if err_type == "less_than_equal":
        return one(f"Too big: expected number to be <={ctx.get('le')}")
    if err_type == "greater_than":
        return one(f"Too small: expected number to be >{ctx.get('gt')}")
    if err_type == "less_than":
        return one(f"Too big: expected number to be <{ctx.get('lt')}")
    if err_type in ("int_from_float", "int_parsing"):
        return one(f"Invalid input: expected int, received {_parsed_type(input_value)}")

    if err_type == "value_error":
        cause = ctx.get("error")
        if isinstance(cause, _ZodTypeError):
            received = _parsed_type(cause.input_value)
            return one(f"Invalid input: expected {cause.expected}, received {received}")
        if isinstance(cause, ZodMultiError):
            return [(message, loc) for message in cause.messages]
        # Custom validators raise ValueError with the verbatim zod message.
        msg: str = err.get("msg", "")
        prefix = "Value error, "
        return one(msg[len(prefix) :] if msg.startswith(prefix) else msg)

    # Fallback: pydantic's own message (should not happen for contract fields).
    return one(err.get("msg", "Invalid input"))


def format_zod_errors(model: type[BaseModel] | None, errors: list[dict[str, Any]]) -> str:
    """zod-validation-error v4 messageBuilder: title-cased issues, '; '-joined."""
    parts: list[str] = []
    for err in errors[:99]:
        for message, loc in _translate(model, err):
            if message:
                message = message[0].upper() + message[1:]
            if loc:
                message += f' at "{_join_path(loc)}"'
            parts.append(message)
    return "Validation error: " + "; ".join(parts)


MAX_ERROR_LENGTH = 1000  # ZodValidationPipe truncates past this


def zod_validate(model: type[BaseModel], data: Any) -> Any:
    # zod `.optional()` accepts absence but rejects explicit null; Pydantic's
    # `X | None` accepts both, so reject nulls up front with zod's wording.
    if isinstance(data, dict):
        for name, field_info in model.model_fields.items():
            nullable = any(isinstance(m, ZodNullable) for m in field_info.metadata)
            if (
                not nullable
                and name in data
                and data[name] is None
                and not field_info.is_required()
                and field_info.default is None
            ):
                expected = _field_expected(model, (name,)) or "undefined"
                raise ApiError(
                    "Validation error: Invalid input: "
                    f"expected {expected}, received null at \"{name}\"",
                    HttpErrorCode.VALIDATION_ERROR,
                )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        message = format_zod_errors(model, exc.errors())
        if len(message) > MAX_ERROR_LENGTH:
            message = message[:MAX_ERROR_LENGTH] + "... (truncated)"
        raise ApiError(message, HttpErrorCode.VALIDATION_ERROR) from exc


class ZodModel(BaseModel):
    """Base for request models: zod strip behaviour + zod-format failures."""

    model_config = ConfigDict(extra="ignore")

    @classmethod
    def zod_validate(cls, data: Any) -> Any:
        return zod_validate(cls, data)
