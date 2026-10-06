"""Rollup aggregation edge semantics (`_apply_rollup`), matched to the oracle.

Empty `average` yields 0 (like sum), not null; the logical rollups and/or/xor
apply only to boolean source values and yield null over other types (e.g.
numeric) or an empty set.
"""

from wedoc.modules.record.computed_cells import _apply_rollup


def test_average_empty_is_zero():
    assert _apply_rollup("average", [], "number") == 0
    assert _apply_rollup("average", [10, 20, 30], "number") == 20


def test_sum_count_empty():
    assert _apply_rollup("sum", [], "number") == 0
    assert _apply_rollup("count", [], "number") == 0


def test_logical_rollups_require_boolean_values():
    # over numeric source values the logical rollups are null
    assert _apply_rollup("and", [10, 20, 30], "boolean") is None
    assert _apply_rollup("or", [10, 20, 30], "boolean") is None
    assert _apply_rollup("xor", [10, 20, 30], "boolean") is None
    # over an empty set they are null too
    assert _apply_rollup("and", [], "boolean") is None


def test_logical_rollups_over_booleans():
    assert _apply_rollup("and", [True, True], "boolean") is True
    assert _apply_rollup("and", [True, False], "boolean") is False
    assert _apply_rollup("or", [False, False], "boolean") is False
    assert _apply_rollup("xor", [True, True], "boolean") is False
    assert _apply_rollup("xor", [True, True, True], "boolean") is True
