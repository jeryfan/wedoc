"""Pin type enum includes routine/Routine/chat (matching the reference's
PinType value|Key interleaving, chat has no PascalCase key)."""

import pytest

from wedoc.core.errors import ApiError
from wedoc.core.validation import zod_validate
from wedoc.modules.pin.schemas import AddPinRo


def test_pin_accepts_routine_and_chat():
    assert zod_validate(AddPinRo, {"type": "routine", "id": "x"}).type == "routine"
    assert zod_validate(AddPinRo, {"type": "chat", "id": "x"}).type == "chat"


def test_pin_type_enum_lists_routine_chat():
    with pytest.raises(ApiError) as exc:
        zod_validate(AddPinRo, {"type": "alien", "id": "x"})
    msg = exc.value.message
    assert msg.endswith('"app"|"App"|"routine"|"Routine"|"chat" at "type"')
