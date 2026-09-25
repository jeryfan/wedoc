"""Pin request schemas — ports packages/openapi/src/pin."""

from ...core.validation import ZodEnumStr, ZodModel

# z.enum(PinType) over a TS string enum accepts BOTH the enum values and their
# PascalCase keys, and the zod error lists them interleaved [value, Key, ...].
PIN_TYPES = [
    "space",
    "Space",
    "base",
    "Base",
    "table",
    "Table",
    "view",
    "View",
    "dashboard",
    "Dashboard",
    "workflow",
    "Workflow",
    "app",
    "App",
]
PinTypeStr = ZodEnumStr(PIN_TYPES)
PositionStr = ZodEnumStr(["before", "after"])


class AddPinRo(ZodModel):
    type: PinTypeStr
    id: str


class DeletePinRo(ZodModel):
    type: PinTypeStr
    id: str


class UpdatePinOrderRo(ZodModel):
    id: str
    type: PinTypeStr
    anchorId: str
    anchorType: PinTypeStr
    position: PositionStr
