"""Short-link request schemas — ports packages/openapi/src/short-link."""

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel

# z.nativeEnum(ShortLinkType) — only the enum values are accepted (unlike the
# pin z.enum case), and the error lists just those values.
SHORT_LINK_TYPES = ["view-share", "base-share", "template", "artifact"]
ShortLinkTypeStr = ZodEnumStr(SHORT_LINK_TYPES)


class CreateShortLinkRo(ZodModel):
    type: ShortLinkTypeStr
    resourceId: str

    @field_validator("resourceId")
    @classmethod
    def _resource_id(cls, value: str) -> str:
        if len(value) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(value) > 50:
            raise ValueError("Too big: expected string to have <=50 characters")
        return value
