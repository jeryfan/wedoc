"""selection ranges validation: each range element must be a [number, number]
pair, so a malformed (extra-nested) shape reports the reference's zod error.
"""

import pytest

from wedoc.core.errors import ApiError
from wedoc.core.validation import zod_validate
from wedoc.modules.selection.schemas import PasteRoBody, RangesRoBody


def test_ranges_accepts_number_pairs():
    body = zod_validate(RangesRoBody, {"ranges": [[0, 0], [1, 2]]})
    assert body.ranges == [(0, 0), (1, 2)]


def test_ranges_rejects_nested_arrays():
    with pytest.raises(ApiError) as exc:
        zod_validate(RangesRoBody, {"ranges": [[[0, 0], [0, 2]]]})
    msg = exc.value.message
    assert 'expected number, received array at "ranges[0][0]"' in msg
    assert 'expected number, received array at "ranges[0][1]"' in msg


def test_ranges_rejects_non_array():
    with pytest.raises(ApiError) as exc:
        zod_validate(RangesRoBody, {"ranges": "notarray"})
    assert 'expected array, received string at "ranges"' in exc.value.message


def test_paste_ranges_validation():
    body = zod_validate(PasteRoBody, {"ranges": [[0, 0], [0, 0]], "content": "x"})
    assert body.ranges == [(0, 0), (0, 0)]
    with pytest.raises(ApiError) as exc:
        zod_validate(PasteRoBody, {"ranges": [[[0, 0], [0, 2]]], "content": "x"})
    assert 'expected number, received array at "ranges[0][0]"' in exc.value.message
