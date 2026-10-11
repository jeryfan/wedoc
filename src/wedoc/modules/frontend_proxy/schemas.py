"""Request contract for storing query parameters."""

from typing import Annotated, Any

from ...core.validation import ZodExpected, ZodModel


class SaveQueryParamsBody(ZodModel):
    params: Annotated[dict[str, Any], ZodExpected("record")]
