"""base-share request schemas — ports packages/openapi/src/base-share/types."""

from typing import Annotated

from pydantic import AfterValidator, StrictBool

from ...core.validation import ZodExpected, ZodModel, ZodNullable


def _share_password(value: str | None) -> str | None:
    # sharePasswordSchema: z.string().min(3)
    if value is None:
        return value
    if len(value) < 3:
        raise ValueError("Too small: expected string to have >=3 characters")
    return value


SharePassword = Annotated[str | None, AfterValidator(_share_password), ZodNullable()]
NullableBool = Annotated[StrictBool | None, ZodNullable()]


class CreateBaseShareRo(ZodModel):
    nodeId: str | None = None


class CopyBaseShareRo(ZodModel):
    spaceId: str
    name: str | None = None
    withRecords: Annotated[StrictBool, ZodExpected("boolean")] = True
    baseId: str | None = None


class UpdateBaseShareRo(ZodModel):
    allowSave: NullableBool = None
    allowCopy: NullableBool = None
    allowEdit: NullableBool = None
    enabled: StrictBool | None = None
    password: SharePassword = None
