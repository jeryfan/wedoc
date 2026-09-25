"""Formula expression support: parser, evaluator and function library.

Read-time evaluation mirrors the reference formula engine
(packages/core/src/formula) but follows wedoc's established computed-field
pattern of evaluating in Python at record read time instead of compiling to
SQL. The public surface here is what the field service (create-time type
inference + reference validation) and record read path (per-record evaluation)
consume.
"""

from .evaluator import evaluate, parsed_value_type, to_cell_value
from .parser import FormulaError, Node, parse, reference_field_ids

__all__ = [
    "FormulaError",
    "Node",
    "evaluate",
    "parse",
    "parsed_value_type",
    "reference_field_ids",
    "to_cell_value",
]
