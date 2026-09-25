"""AST visitor: type inference (create) and per-record evaluation (read).

Ports packages/formula ``EvalVisitor``. ``parsed_value_type`` runs the visitor
without a record to derive ``(cellValueType, isMultipleCellValue)``; ``evaluate``
runs it against one record's field values. Field references read the API-shaped
cell value from ``record_fields`` keyed by field id, exactly like the reference
``record.fields[field.id]``.
"""

from __future__ import annotations

from typing import Any

from .functions import FormulaBaseErrorRaised, convert_typed_value, resolve_function
from .parser import Bin, Bool, Call, FieldRef, FormulaError, Neg, Node, Num, Str
from .values import (
    BOOLEAN,
    DATETIME,
    NUMBER,
    STRING,
    FormulaBaseError,
    TypedValue,
    cell_value_to_string,
    js_mod,
    js_str,
    js_truthy,
    loose_eq,
    normalize_number_output,
    relational,
    strict_eq,
    to_number,
)

_COMPARISON = {"=", "!=", ">", ">=", "<", "<="}
_ERROR_TV = TypedValue(FormulaBaseError(), STRING, False)

# EVALUATOR_MARKER


class Evaluator:
    def __init__(
        self,
        dependencies: dict[str, dict[str, Any]],
        record_fields: dict[str, Any] | None,
        timezone: str = "UTC",
    ) -> None:
        self.dependencies = dependencies
        self.record_fields = record_fields
        self.has_record = record_fields is not None
        self.timezone = timezone

    def visit(self, node: Node) -> TypedValue:
        if isinstance(node, Num):
            return TypedValue(node.value, NUMBER)
        if isinstance(node, Str):
            return TypedValue(node.value, STRING)
        if isinstance(node, Bool):
            return TypedValue(node.value, BOOLEAN)
        if isinstance(node, FieldRef):
            return self._field(node.id)
        if isinstance(node, Neg):
            return self._unary(node)
        if isinstance(node, Bin):
            return self._binary(node)
        if isinstance(node, Call):
            return self._call(node)
        return TypedValue(None, STRING)

    def _field(self, field_id: str) -> TypedValue:
        field = self.dependencies.get(field_id)
        if field is None:
            raise FormulaBaseErrorRaised(f"FieldId {field_id} is a invalid field id")
        cvt = field["cell_value_type"]
        is_multiple = bool(field.get("is_multiple_cell_value"))
        value = self.record_fields.get(field_id) if self.has_record else None
        if cvt == NUMBER:
            return TypedValue(self._norm_number(value, is_multiple), NUMBER, is_multiple, field)
        if value is None or cvt not in (STRING, DATETIME):
            return TypedValue(value, cvt, is_multiple, field)
        if is_multiple and isinstance(value, list) and value and isinstance(value[0], (dict, list)):
            value = [cell_value_to_string(field, v) for v in value]
        elif not is_multiple and isinstance(value, (dict, list)):
            value = cell_value_to_string(field, value)
        return TypedValue(value, cvt, is_multiple, field)

    @staticmethod
    def _norm_number(value: Any, is_multiple: bool) -> Any:
        def one(cell: Any) -> Any:
            if cell is None or cell == "":
                return None
            return cell if isinstance(cell, (int, float)) else to_number(cell)

        if is_multiple:
            return [one(v) for v in value] if isinstance(value, list) else value
        return one(value)

    # BINARY_MARKER

    def _binary(self, node: Bin) -> TypedValue:
        op = node.op
        left = self.visit(node.left)
        right = self.visit(node.right)
        is_comparison = op in _COMPARISON
        lt = self._transform_node_value(left, is_comparison)
        rt = self._transform_node_value(right, is_comparison)
        lv = lt.value if lt is not None else None
        rv = rt.value if rt is not None else None
        value_type = self._binary_type(op, left, right)
        value = self._apply_binary(op, value_type, left, right, lv, rv)
        return TypedValue(value, value_type)

    @staticmethod
    def _binary_type(op: str, left: TypedValue, right: TypedValue) -> str:
        if op == "+":
            return NUMBER if left.type == NUMBER and right.type == NUMBER else STRING
        if op in ("-", "*", "%", "/"):
            return NUMBER
        if op == "&":
            return STRING
        return BOOLEAN

    def _apply_binary(
        self, op: str, value_type: str, left: TypedValue, right: TypedValue, lv: Any, rv: Any
    ) -> Any:
        if op == "*":
            return to_number(lv) * to_number(rv)
        if op == "/":
            return None if not js_truthy(rv) else to_number(lv) / to_number(rv)
        if op == "%":
            return None if not js_truthy(rv) else js_mod(to_number(lv), to_number(rv))
        if op == "-":
            return to_number(lv) - to_number(rv)
        if op == "+":
            if value_type == NUMBER:
                return to_number(lv) + to_number(rv)
            return self._concat(lv, rv)
        if op == "&":
            return self._concat(lv, rv)
        if op in (">", "<", ">=", "<="):
            return relational(lv, rv, op)
        if op == "=":
            return self._equal(left, right, lv, rv)
        if op == "!=":
            return not self._equal(left, right, lv, rv)
        if op == "&&":
            return lv and rv
        if op == "||":
            return lv or rv
        raise FormulaBaseErrorRaised(f"unsupported operator {op}")

    @staticmethod
    def _concat(lv: Any, rv: Any) -> str:
        return ("" if lv is None else js_str(lv)) + ("" if rv is None else js_str(rv))

    def _transform_node_value(self, tv: TypedValue, is_comparison: bool) -> TypedValue | None:
        if tv.field is None:
            return tv
        cvt = tv.field["cell_value_type"]
        if cvt == DATETIME and is_comparison:
            return tv
        if tv.field.get("is_multiple_cell_value") and cvt == NUMBER:
            if not tv.value:
                return None
            if isinstance(tv.value, list) and len(tv.value) > 1:
                raise FormulaBaseErrorRaised(
                    "Cannot perform mathematical calculations on an array with more "
                    "than one numeric element."
                )
            first = tv.value[0] if isinstance(tv.value, list) else tv.value
            return TypedValue(to_number(first), NUMBER)
        if cvt in (NUMBER, BOOLEAN, STRING):
            return tv
        return TypedValue(cell_value_to_string(tv.field, tv.value), STRING)

    def _unary(self, node: Neg) -> TypedValue:
        tv = self.visit(node.operand)
        transformed = self._transform_unary(tv)
        value = transformed.value if transformed is not None else None
        result = -to_number(value) if js_truthy(value) else None
        return TypedValue(result, NUMBER)

    @staticmethod
    def _transform_unary(tv: TypedValue) -> TypedValue | None:
        if tv.field is None:
            return tv
        if tv.field["cell_value_type"] != NUMBER:
            return None
        if tv.field.get("is_multiple_cell_value"):
            if not tv.value:
                return None
            if isinstance(tv.value, list) and len(tv.value) > 1:
                raise FormulaBaseErrorRaised(
                    "Cannot perform mathematical calculations on an array with more "
                    "than one numeric element."
                )
            first = tv.value[0] if isinstance(tv.value, list) else tv.value
            return TypedValue(to_number(first), NUMBER)
        return tv

    # EQUALITY_MARKER

    @staticmethod
    def _is_string_like(tv: TypedValue) -> bool:
        return tv.type == STRING or (tv.field is not None and tv.field["cell_value_type"] == STRING)

    @staticmethod
    def _is_numeric_like(tv: TypedValue) -> bool:
        return tv.type == NUMBER or (tv.field is not None and tv.field["cell_value_type"] == NUMBER)

    def _is_blank_equality(self, tv: TypedValue, value: Any) -> bool:
        if tv.is_blank or value is None:
            return True
        return self._is_string_like(tv) and value == ""

    def _equal(self, left: TypedValue, right: TypedValue, lv: Any, rv: Any) -> bool:
        normalize = (
            self._is_string_like(left)
            or self._is_string_like(right)
            or self._is_numeric_like(left)
            or self._is_numeric_like(right)
        )
        if normalize:
            nl, nr = self._norm_blank(left, lv), self._norm_blank(right, rv)
        else:
            nl, nr = lv, rv
        has_numeric = self._is_numeric_like(left) or self._is_numeric_like(right)
        strict = has_numeric and (
            self._is_blank_equality(left, lv) or self._is_blank_equality(right, rv)
        )
        return strict_eq(nl, nr) if strict else loose_eq(nl, nr)

    def _norm_blank(self, tv: TypedValue, value: Any) -> Any:
        if value is None and (self._is_string_like(tv) or self._is_numeric_like(tv)):
            return ""
        return value

    def _call(self, node: Call) -> TypedValue:
        func = resolve_function(node.name)
        if func is None:
            raise FormulaBaseErrorRaised(f"Function name {node.name} is not found")
        if func.name == "BLANK":
            return TypedValue(None, STRING, False, None, True)
        try:
            params = [convert_typed_value(self.visit(arg), func) for arg in node.args]
        except FormulaError:
            if func.name != "IS_ERROR":
                raise
            params = [_ERROR_TV]
        type_, is_multiple = func.return_type(params)
        if not self.has_record:
            return TypedValue(None, type_, is_multiple)
        ctx = {
            "record_fields": self.record_fields,
            "dependencies": self.dependencies,
            "timeZone": self.timezone,
        }
        value = func.eval(params, ctx)
        return TypedValue(value, type_, bool(is_multiple))


# PUBLIC_MARKER


def parsed_value_type(tree: Node, dependencies: dict[str, dict[str, Any]]) -> tuple[str, bool]:
    """Return ``(cellValueType, isMultipleCellValue)`` without a record."""
    typed = Evaluator(dependencies, None).visit(tree)
    return (typed.type, bool(typed.is_multiple))


def evaluate(
    tree: Node,
    dependencies: dict[str, dict[str, Any]],
    record_fields: dict[str, Any],
    timezone: str = "UTC",
) -> TypedValue:
    return Evaluator(dependencies, record_fields, timezone).visit(tree)


def _scalar_cell(value: Any) -> Any:
    if value is False:
        return None
    if isinstance(value, FormulaBaseError):
        return None
    if isinstance(value, float):
        return normalize_number_output(value)
    return value


def to_cell_value(typed: TypedValue) -> Any:
    """Collapse a TypedValue to the API cell value (toPlain + number parity)."""
    value = typed.value
    if isinstance(value, FormulaBaseError):
        return None
    if typed.is_multiple and isinstance(value, list):
        collapsed = [_scalar_cell(v) for v in value]
        return collapsed or None
    return _scalar_cell(value)
