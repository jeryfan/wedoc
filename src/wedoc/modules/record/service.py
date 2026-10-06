"""record domain service — ports features/record (record-open-api.service.ts).

Slice A: CRUD + list queries (viewId/filter/sort/projection/take/skip/cursor).
history/form-submit/TQL search land in later slices; attachments need the
storage stack; collaborators and socket endpoints are M3 realtime.
"""

import base64
import calendar
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, is_valid_prefixed_id, new_id
from ...core.storage import get_public_full_storage_url
from ..field import repository as field_repository
from ..field.repository import get_table_meta_by_id
from ..table import repository as table_repository
from ..view.repository import get_view_row
from . import computed_cells, repository
from .cell_format import cell_value_to_string
from .link_cells import (
    LinkFieldContext,
    link_input_ids,
    own_row_fk_values,
    refresh_symmetric_cells,
    resolve_link_cell,
    sync_relation,
)
from .schemas import (
    RecordBulkPatchBody,
    RecordCreateBody,
    RecordInsertOrder,
    RecordItem,
    RecordPatchBody,
    RecordSubmitBody,
)
from .tql import TqlParseError, parse_tql

DEFAULT_TAKE = 100
# cell types whose value is stored JSON-serialized (json.dumps on write,
# json.loads on read). All use a jsonb column except single-value user, whose
# column is text but still holds one JSON object (see provider.column_type_for).
JSONB_FIELD_TYPES = {"user", "attachment", "button", "multipleSelect"}
SYSTEM_COMPUTED_FIELD_TYPES = {
    "createdTime",
    "lastModifiedTime",
    "createdBy",
    "lastModifiedBy",
    "autoNumber",
}

# number fields created without explicit options carry these auto-filled
# display defaults in wedoc's field row; the reference leaves the row NULL and
# derives the same defaults when serializing, so history must treat them as
# "not set".
HISTORY_DEFAULT_OPTIONS = {"number": {"formatting": {"type": "decimal", "precision": 2}}}

FILTER_OPERATORS = {
    "is": "= :{p}",
    "isNot": "IS DISTINCT FROM :{p}",
    "contains": "LIKE :{p}",
    "doesNotContain": "NOT LIKE :{p}",
    "isGreater": "> :{p}",
    "isGreaterEqual": ">= :{p}",
    "isLess": "< :{p}",
    "isLessEqual": "<= :{p}",
}

# operator enum accepted by the reference filter schema; anything outside it
# fails request validation before reaching the query builder.
_FILTER_ENUM = set(FILTER_OPERATORS) | {"isEmpty", "isNotEmpty"}

# getValidFilterOperators (operator.ts): the per-field-type operator allow-list.
# An operator outside a field's set fails request validation with a 400 before
# any SQL is built (which also stops type-mismatched operators crashing at SQL).
_TEXT_FILTER_OPS = ["is", "isNot", "contains", "doesNotContain", "isEmpty", "isNotEmpty"]
_NUMBER_FILTER_OPS = [
    "is", "isNot", "isGreater", "isGreaterEqual", "isLess", "isLessEqual",
    "isEmpty", "isNotEmpty",
]
_BOOLEAN_FILTER_OPS = ["is"]
_DATETIME_FILTER_OPS = [
    "is", "isNot", "isWithIn", "isBefore", "isAfter", "isOnOrBefore", "isOnOrAfter",
    "isEmpty", "isNotEmpty",
]
_SELECT_MULTI_FILTER_OPS = [
    "hasAnyOf", "hasAllOf", "isExactly", "isNotExactly", "hasNoneOf", "isEmpty", "isNotEmpty",
]


def _valid_filter_operators(field: dict[str, Any]) -> set[str]:
    cvt = field.get("cell_value_type")
    ftype = field.get("type")
    multi = bool(field.get("is_multiple_cell_value"))
    if cvt == "string":
        ops = list(_TEXT_FILTER_OPS)
    elif cvt == "number":
        ops = list(_NUMBER_FILTER_OPS)
    elif cvt == "boolean":
        ops = list(_BOOLEAN_FILTER_OPS)
    elif cvt == "dateTime":
        ops = list(_DATETIME_FILTER_OPS)
    else:
        ops = []
    if ftype == "singleSelect":
        if multi:
            ops = list(_SELECT_MULTI_FILTER_OPS)
        else:
            ops = [o for o in ops if o not in ("contains", "doesNotContain")]
            ops += ["isAnyOf", "isNoneOf"]
    elif ftype == "multipleSelect":
        ops = list(_SELECT_MULTI_FILTER_OPS)
    elif ftype in ("user", "createdBy", "lastModifiedBy", "link"):
        ops = (
            ["hasAnyOf", "hasAllOf", "isExactly", "hasNoneOf", "isNotExactly"]
            if multi
            else ["is", "isNot", "isAnyOf", "isNoneOf"]
        )
        if ftype == "link":
            ops += ["contains", "doesNotContain"]
        ops += ["isEmpty", "isNotEmpty"]
    elif ftype == "attachment":
        ops = ["isEmpty", "isNotEmpty"]
    return set(ops)


# date-field filter operators (dateTimeFieldValidOperators). is/isWithIn compile
# to a BETWEEN over the mode's [start, end] range; the scalar comparison aliases
# (isGreater/isLess/...) map onto the isAfter/isBefore handlers exactly as the
# reference cell-value-filter compiler does.
_DATE_OP_ALIAS = {
    "isGreater": "isAfter",
    "isGreaterEqual": "isOnOrAfter",
    "isLess": "isBefore",
    "isLessEqual": "isOnOrBefore",
}

# createdBy/lastModifiedBy fields carry no physical column; their value lives in
# the row's system columns, so filters must target those (bare user id text).
_SYSTEM_USER_FILTER_COLUMN = {
    "createdBy": "__created_by",
    "lastModifiedBy": "__last_modified_by",
}

# createdTime/lastModifiedTime likewise resolve from system timestamptz columns.
_SYSTEM_DATE_FILTER_COLUMN = {
    "createdTime": "__created_time",
    "lastModifiedTime": "__last_modified_time",
}


def _jsonpath_escape(value: str) -> str:
    # escape for embedding inside a jsonpath double-quoted like_regex literal.
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _invalid_filter_operator() -> ApiError:
    return ApiError(
        "Invalid record condition operator for field",
        HttpErrorCode.VALIDATION_ERROR,
        {"domainCode": "validation.invalid", "domainTags": ["validation"]},
    )


def _date_range_error(message: str) -> ApiError:
    # mirrors getFilterDateTimeRange's BadRequestException for a date filter
    # value missing a part its mode requires (numberOfDays / exactDate / range).
    return ApiError(message, HttpErrorCode.VALIDATION_ERROR)


def _date_filter_range(
    value: dict[str, Any], field_options: dict[str, Any]
) -> tuple[datetime, datetime]:
    """Resolve a ``{mode, ...}`` date filter to a ``[start, end]`` UTC range.

    Port of the reference ``getFilterDateTimeRange`` (cell-value-filter.abstract).
    The range is computed in the field's ``formatting.timeZone`` (the reference
    reads the field options, not the filter value's timeZone) using stdlib
    ``datetime``/``zoneinfo`` instead of dayjs. Faithful semantics: the ISO week
    starts on Monday; ``startOf('day')`` is 00:00:00.000 and ``endOf('day')`` is
    23:59:59.999; month/year arithmetic clamps the day-of-month.
    """
    formatting = field_options.get("formatting") if isinstance(field_options, dict) else None
    formatting = formatting if isinstance(formatting, dict) else {}
    tz_name = formatting.get("timeZone") or value.get("timeZone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    date_format = formatting.get("date")
    time_format = formatting.get("time")
    has_time = bool(time_format) and time_format != "None"

    mode = value.get("mode")
    number_of_days = value.get("numberOfDays")

    def start_of_day(d: datetime) -> datetime:
        return d.replace(hour=0, minute=0, second=0, microsecond=0)

    def end_of_day(d: datetime) -> datetime:
        return d.replace(hour=23, minute=59, second=59, microsecond=999000)

    def add_months(d: datetime, months: int) -> datetime:
        total = d.year * 12 + (d.month - 1) + months
        year, month0 = divmod(total, 12)
        month = month0 + 1
        day = min(d.day, calendar.monthrange(year, month)[1])
        return d.replace(year=year, month=month, day=day)

    def start_of_week(d: datetime) -> datetime:  # weekStart = Monday
        return start_of_day(d - timedelta(days=d.weekday()))

    def end_of_week(d: datetime) -> datetime:
        return end_of_day(d + timedelta(days=6 - d.weekday()))

    def start_of_month(d: datetime) -> datetime:
        return start_of_day(d.replace(day=1))

    def end_of_month(d: datetime) -> datetime:
        return end_of_day(d.replace(day=calendar.monthrange(d.year, d.month)[1]))

    def start_of_year(d: datetime) -> datetime:
        return start_of_day(d.replace(month=1, day=1))

    def end_of_year(d: datetime) -> datetime:
        return end_of_day(d.replace(month=12, day=31))

    def parse_exact(raw: Any) -> datetime:
        if not isinstance(raw, str) or not raw:
            raise _date_range_error("Exact date must be entered")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(tz).replace(tzinfo=None)

    def fixed_day(day: datetime) -> tuple[datetime, datetime]:
        return start_of_day(day), end_of_day(day)

    now = datetime.now(UTC).astimezone(tz).replace(tzinfo=None)

    if mode == "today":
        start, end = fixed_day(now)
    elif mode == "tomorrow":
        start, end = fixed_day(now + timedelta(days=1))
    elif mode == "yesterday":
        start, end = fixed_day(now - timedelta(days=1))
    elif mode == "oneWeekAgo":
        start, end = fixed_day(now - timedelta(weeks=1))
    elif mode == "oneWeekFromNow":
        start, end = fixed_day(now + timedelta(weeks=1))
    elif mode == "oneMonthAgo":
        start, end = fixed_day(add_months(now, -1))
    elif mode == "oneMonthFromNow":
        start, end = fixed_day(add_months(now, 1))
    elif mode in ("daysAgo", "daysFromNow"):
        if not number_of_days:  # reference computeOffsetDays rejects 0/absent
            raise _date_range_error("Number of days must be entered")
        offset = timedelta(days=number_of_days)
        start, end = fixed_day(now - offset if mode == "daysAgo" else now + offset)
    elif mode == "exactDate":
        parsed = parse_exact(value.get("exactDate"))
        start, end = start_of_day(parsed), end_of_day(parsed)
    elif mode == "exactDateTime":
        parsed = parse_exact(value.get("exactDate"))
        start, end = parsed, parsed
    elif mode == "exactFormatDate":
        parsed = parse_exact(value.get("exactDate"))
        if date_format == "YYYY":
            start, end = start_of_year(parsed), end_of_year(parsed)
        elif date_format in ("YYYY-MM", "MM"):
            start, end = start_of_month(parsed), end_of_month(parsed)
        else:
            start, end = start_of_day(parsed), end_of_day(parsed)
    elif mode == "dateRange":
        first = parse_exact(value.get("exactDate"))
        raw_end = value.get("exactDateEnd")
        if not isinstance(raw_end, str) or not raw_end:
            raise _date_range_error("End date must be entered for date range")
        last = parse_exact(raw_end)
        if first > last:
            raise _date_range_error("Start date cannot be after end date")
        start, end = (first, last) if has_time else (start_of_day(first), end_of_day(last))
    elif mode in ("currentWeek", "lastWeek", "nextWeekPeriod"):
        cursor = now
        if mode == "lastWeek":
            cursor = now - timedelta(weeks=1)
        elif mode == "nextWeekPeriod":
            cursor = now + timedelta(weeks=1)
        start, end = start_of_week(cursor), end_of_week(cursor)
    elif mode in ("currentMonth", "lastMonth", "nextMonthPeriod"):
        cursor = now
        if mode == "lastMonth":
            cursor = add_months(now, -1)
        elif mode == "nextMonthPeriod":
            cursor = add_months(now, 1)
        start, end = start_of_month(cursor), end_of_month(cursor)
    elif mode in ("currentYear", "lastYear", "nextYearPeriod"):
        cursor = now
        if mode == "lastYear":
            cursor = add_months(now, -12)
        elif mode == "nextYearPeriod":
            cursor = add_months(now, 12)
        start, end = start_of_year(cursor), end_of_year(cursor)
    elif mode == "pastWeek":
        start, end = start_of_day(now - timedelta(weeks=1)), end_of_day(now)
    elif mode == "pastMonth":
        start, end = start_of_day(add_months(now, -1)), end_of_day(now)
    elif mode == "pastYear":
        start, end = start_of_day(add_months(now, -12)), end_of_day(now)
    elif mode == "nextWeek":
        start, end = start_of_day(now), end_of_day(now + timedelta(weeks=1))
    elif mode == "nextMonth":
        start, end = start_of_day(now), end_of_day(add_months(now, 1))
    elif mode == "nextYear":
        start, end = start_of_day(now), end_of_day(add_months(now, 12))
    elif mode in ("pastNumberOfDays", "nextNumberOfDays"):
        if number_of_days is None:  # reference generateOffsetDateRange allows 0
            raise _date_range_error("Number of days must be entered")
        offset = timedelta(days=number_of_days)
        if mode == "pastNumberOfDays":
            start, end = start_of_day(now - offset), end_of_day(now)
        else:
            start, end = start_of_day(now), end_of_day(now + offset)
    else:
        raise _date_range_error(f"Unsupported date filter mode: {mode}")

    return start.replace(tzinfo=tz).astimezone(UTC), end.replace(tzinfo=tz).astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    """Inverse of _iso: ISO string (naive-UTC) datetime for audit columns."""
    if not value or not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _record_not_found(localization: bool = False) -> ApiError:
    if localization:
        return ApiError(
            "Record not found",
            HttpErrorCode.NOT_FOUND,
            {"localization": {"i18nKey": "httpErrors.record.notFound"}},
        )
    return ApiError(
        "Record not found",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "record.not_found", "domainTags": ["not-found"]},
    )


def _require_record_id(record_id: str) -> None:
    if not is_valid_prefixed_id(record_id, "rec"):
        raise ApiError(
            "Invalid RecordId",
            HttpErrorCode.VALIDATION_ERROR,
            {"domainCode": "validation.invalid", "domainTags": ["validation"]},
        )


def _unique_violation_error(
    exc: IntegrityError, fields: list[dict[str, Any]], operation: str
) -> ApiError | None:
    """Map a postgres constraint violation to the reference's domain error: a
    ``uq_<fieldId>`` index -> the field's unique-value error (field round-trips
    out of the constraint name); an ``index_<fkcol>`` link FK -> the one-to-one
    duplicate error; a link foreign-key violation (23503) -> the missing linked
    record error. operation is ``insert`` or ``update``."""
    # The asyncpg error is wrapped by SQLAlchemy's DBAPI adapter; walk the cause
    # chain for the 23505 (unique_violation) / 23503 (foreign_key_violation)
    # sqlstate and its constraint name, falling back to parsing the message.
    constraint = ""
    sqlstate = ""
    node: Any = getattr(exc, "orig", None)
    seen: set[int] = set()
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        state = getattr(node, "sqlstate", None)
        if state in ("23505", "23503"):
            sqlstate = state
            constraint = getattr(node, "constraint_name", None) or ""
            break
        node = getattr(node, "__cause__", None)
    if not constraint:
        # SQLAlchemy's wrapper may expose the sqlstate but not the constraint
        # name; recover the constraint (and sqlstate, if still unknown) from the
        # rendered message.
        match = re.search(r'unique constraint "([^"]+)"', str(exc))
        if match is not None:
            constraint = match.group(1)
            sqlstate = sqlstate or "23505"
        else:
            match = re.search(r'foreign key constraint "([^"]+)"', str(exc))
            if match is not None:
                constraint = match.group(1)
                sqlstate = sqlstate or "23503"
    if not constraint:
        return None
    # link foreign-key violation: the written link value points at a record that
    # does not exist (the FK column on the host row is checked at insert/update).
    if sqlstate == "23503":
        fk_match = re.search(r"__fk_(fld[0-9A-Za-z]+)", constraint)
        data: dict[str, Any] = {
            "domainCode": "validation.link.invalid_reference",
            "domainTags": ["validation"],
        }
        if fk_match is not None:
            data["details"] = {"fieldId": fk_match.group(1), "fieldType": "link"}
        return ApiError(
            f"Cannot complete {operation}: a linked record does not exist",
            HttpErrorCode.VALIDATION_ERROR,
            data,
        )
    # link one-one FK unique constraint (index_<fkcol>): the target record is
    # already linked by another record in a one-to-one relationship.
    if constraint.startswith("index_"):
        return ApiError(
            f"Cannot complete {operation}: the target record is already linked "
            "by another record in a one-to-one relationship",
            HttpErrorCode.VALIDATION_ERROR,
            {
                "domainCode": "validation.link.one_one_duplicate",
                "domainTags": ["validation"],
                "localization": {"i18nKey": "httpErrors.custom.linkOneOneDuplicate"},
            },
        )
    if not constraint.startswith("uq_"):
        return None
    field_id = constraint[3:]
    field = next((f for f in fields if f["id"] == field_id), None)
    data = {"domainCode": "validation.field.unique", "domainTags": ["validation"]}
    if field is not None:
        data["details"] = {"fieldId": field["id"], "fieldName": field["name"]}
        data["localization"] = {
            "i18nKey": "httpErrors.custom.recordFieldValueDuplicate",
            "context": {"fieldName": field["name"]},
        }
    return ApiError(
        f"Cannot complete {operation}: field {field_id} must have a unique value",
        HttpErrorCode.VALIDATION_ERROR,
        data,
    )


def _js_typeof(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _invalid_cell_value(field: dict[str, Any], value: Any, detail: str) -> ApiError:
    return ApiError(
        f'Invalid value for field "{field["name"]}": \u2716 {detail}',
        HttpErrorCode.VALIDATION_ERROR,
        {
            "domainCode": "validation.field.invalid_value",
            "domainTags": ["validation"],
            "details": {
                "fieldId": field["id"],
                "fieldName": field["name"],
                "fieldType": field["type"],
                "providedValue": value,
            },
        },
    )


_VALIDATED_CELL_TYPES = {
    "number", "rating", "checkbox", "date", "singleLineText", "longText",
    "singleSelect", "multipleSelect",
}


def _is_link_item(item: Any) -> bool:
    # linkCellValueSchema: { id: string().startsWith('rec'), title: string().nullish() }
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].startswith("rec")
        and (item.get("title") is None or isinstance(item.get("title"), str))
    )


def _validate_link_value(field: dict[str, Any], value: Any) -> None:
    # single: {obj}|[obj,...]; multiple: [obj,...]|{obj}. Any other shape fails the
    # union and the reference reports the generic zod message ``Invalid input``.
    if isinstance(value, list):
        if value and all(_is_link_item(x) for x in value):
            return
    elif _is_link_item(value):
        return
    raise _invalid_cell_value(field, value, "Invalid input")


def _validate_user_value(field: dict[str, Any], value: Any) -> None:
    # userCellValueSchema is an object (multiple wraps it in an array); a wrong
    # base type reports the zod ``expected object|array, received T`` message.
    if field.get("is_multiple_cell_value"):
        if not isinstance(value, list):
            raise _invalid_cell_value(
                field, value, f"Invalid input: expected array, received {_js_typeof(value)}"
            )
        for item in value:
            if not isinstance(item, dict):
                raise _invalid_cell_value(
                    field, value, f"Invalid input: expected object, received {_js_typeof(item)}"
                )
    elif not isinstance(value, dict):
        raise _invalid_cell_value(
            field, value, f"Invalid input: expected object, received {_js_typeof(value)}"
        )


def _validate_cell_value(field: dict[str, Any], value: Any) -> None:
    """Reject a wrong-type/out-of-range cell value the way the reference does
    (zod cellValue schema): raises a 400 ``Invalid value for field "X": ✖ ...``.
    Structured types (link/user/attachment) only get their shape checked here;
    existence of the referenced record/user/attachment is enforced on write."""
    ftype = field["type"]
    if value is None:
        return
    if ftype == "link":
        _validate_link_value(field, value)
        return
    if ftype == "user":
        _validate_user_value(field, value)
        return
    if ftype == "attachment":
        if not isinstance(value, list):
            raise _invalid_cell_value(
                field, value, f"Invalid input: expected array, received {_js_typeof(value)}"
            )
        return
    if ftype not in _VALIDATED_CELL_TYPES:
        return
    jt = _js_typeof(value)

    def wrong_type(expected: str) -> None:
        raise _invalid_cell_value(
            field, value, f"Invalid input: expected {expected}, received {jt}"
        )

    if ftype == "number":
        if jt != "number":
            wrong_type("number")
    elif ftype == "rating":
        mx = int((json.loads(field.get("options") or "{}").get("max")) or 10)
        if jt != "number":
            wrong_type("number")
        if isinstance(value, float) and not value.is_integer():
            raise _invalid_cell_value(field, value, "Invalid input: expected int, received number")
        if value > mx:
            raise _invalid_cell_value(field, value, f"Too big: expected number to be <={mx}")
        if value < 1:
            raise _invalid_cell_value(field, value, "Too small: expected number to be >=1")
    elif ftype == "checkbox":
        if jt != "boolean":
            wrong_type("boolean")
    elif ftype in ("singleLineText", "longText"):
        if jt != "string":
            wrong_type("string")
    elif ftype == "singleSelect":
        _validate_select_value(field, value, multiple=False)
    elif ftype == "multipleSelect":
        _validate_select_value(field, value, multiple=True)
    elif ftype == "date":
        if jt != "string" or not _is_iso_datetime(value):
            raise _invalid_cell_value(field, value, "Invalid ISO datetime")


def _select_enum_values(field: dict[str, Any]) -> list[str]:
    choices = (json.loads(field.get("options") or "{}").get("choices")) or []
    values: list[str] = []
    for ch in choices:
        for v in (ch.get("id"), ch.get("name")):
            if v is not None and v not in values:
                values.append(v)
    return values


def _validate_select_value(field: dict[str, Any], value: Any, multiple: bool) -> None:
    enum = _select_enum_values(field)
    expected = "Invalid option: expected one of " + "|".join(f'"{v}"' for v in enum)
    if multiple:
        if not isinstance(value, list):
            raise _invalid_cell_value(
                field, value, f"Invalid input: expected array, received {_js_typeof(value)}"
            )
        for item in value:
            if item not in enum:
                raise _invalid_cell_value(field, value, expected)
    elif value not in enum:
        raise _invalid_cell_value(field, value, expected)


def _is_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


def _anchor_not_found(anchor_id: str) -> ApiError:
    return ApiError(
        f"Anchor record not found: {anchor_id}",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "record.not_found", "domainTags": ["not-found"]},
    )


# Number.EPSILON * 2 — the reference's zero-gap threshold before a shuffle.
_ORDER_EPSILON = 2.220446049250313e-16 * 2


def _field_options(field: dict[str, Any]) -> dict[str, Any]:
    raw = field.get("options")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return {}


def _is_computed_field(field: dict[str, Any]) -> bool:
    """Fields whose cell value is derived, not stored from a user edit.

    Their physical column is materialized only so filter/sort/group/aggregation
    have a real value to read; the record VO always comes from the read-time
    resolvers, so the VO never reads these columns back.
    """
    return bool(
        field.get("is_lookup")
        or field["type"] in ("rollup", "conditionalRollup")
        or (field["type"] == "formula" and not field.get("is_lookup"))
    )


def history_options(field_type: str, options_raw: str | None) -> Any:
    # ref stores NULL options on the field row and derives display defaults
    # in the VO; history rows therefore carry options: null for fields that
    # never set them explicitly (incl. auto-filled type defaults).
    if not options_raw:
        return None
    options = json.loads(options_raw)
    if not options:
        return None
    if options == HISTORY_DEFAULT_OPTIONS.get(field_type):
        return None
    return options


def build_history_row(
    *,
    table_id: str,
    record_id: str,
    field_id: str,
    name: str,
    field_type: str,
    options_raw: str | None,
    cell_value_type: str,
    before: Any,
    after: Any,
    user_id: str,
) -> dict[str, Any] | None:
    """One record_history row for a field value change; None when unchanged."""
    if before == after:
        return None
    meta = {
        "type": field_type,
        "name": name,
        "options": history_options(field_type, options_raw),
        "cellValueType": cell_value_type,
    }
    return {
        "id": new_id(IdPrefix.RECORD_HISTORY),
        "table_id": table_id,
        "record_id": record_id,
        "field_id": field_id,
        "before": json.dumps({"meta": meta, "data": before}, separators=(",", ":")),
        "after": json.dumps({"meta": meta, "data": after}, separators=(",", ":")),
        "created_by": user_id,
    }


class RecordService:
    async def _load_context(self, table_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        table = await get_table_meta_by_id(table_id)
        if table is None or table["deleted_time"] is not None:
            raise ApiError(
                "Table not found",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "table.not_found", "domainTags": ["not-found"]},
            )
        fields = await table_repository.list_field_rows(table_id)
        return table, fields

    async def _link_ctx(
        self, field: dict[str, Any], cache: dict[str, Any]
    ) -> LinkFieldContext | None:
        fid = field["id"]
        if fid in cache:
            return cache[fid]
        options = _field_options(field)
        foreign_table_id = options.get("foreignTableId")
        foreign_table = (
            await get_table_meta_by_id(foreign_table_id) if foreign_table_id else None
        )
        if foreign_table is None:
            cache[fid] = None
            return None
        foreign_fields = await table_repository.list_field_rows(foreign_table["id"])
        lookup = next(
            (f for f in foreign_fields if f["id"] == options.get("lookupFieldId")), None
        ) or next((f for f in foreign_fields if f.get("is_primary")), None)
        if lookup is None:
            cache[fid] = None
            return None
        ctx = LinkFieldContext(field, options, foreign_table, lookup)
        ctx.sym_field = next(
            (f for f in foreign_fields if f["id"] == options.get("symmetricFieldId")), None
        )
        cache[fid] = ctx
        return ctx

    async def _sync_link_write(
        self,
        table: dict[str, Any],
        ctx: LinkFieldContext,
        record_id: str,
        prev_ids: list[str],
        new_ids: list[str],
        self_primary_col: str | None,
    ) -> None:
        affected = set(prev_ids) | set(new_ids)
        touched = await sync_relation(
            ctx, table["base_id"], table["db_table_name"], record_id, new_ids
        )
        affected |= touched
        await refresh_symmetric_cells(
            ctx, table["db_table_name"], self_primary_col or "", affected, ctx.sym_field
        )

    async def _ensure_foreign_records_exist(
        self, ctx: LinkFieldContext, ids: list[str], operation: str
    ) -> None:
        """Pre-check that a junction-written link points at existing records.

        ManyOne/OneOne keep the FK on the host row, so a missing target trips the
        insert/update FK constraint and is mapped by ``_unique_violation_error``.
        ManyMany / one-way OneMany write the junction in a separate transaction
        after the host row commits; a missing target there would orphan the row
        and surface as a 500, so it is rejected up front the way the reference's
        transactional insert does (``a linked record does not exist``)."""
        if not ids:
            return
        junction = ctx.relationship == "manyMany" or (
            ctx.relationship == "oneMany" and bool(ctx.options.get("isOneWay"))
        )
        if not junction:
            return
        found = await ctx.resolve_titles(ids)
        if any(i not in found for i in ids):
            raise ApiError(
                f"Cannot complete {operation}: a linked record does not exist",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "domainCode": "validation.link.invalid_reference",
                    "domainTags": ["validation"],
                    "details": {"fieldId": ctx.field["id"], "fieldType": "link"},
                },
            )

    def _field_by_key(
        self, fields: list[dict[str, Any]], key: str, key_type: str
    ) -> dict[str, Any]:
        column = "name" if key_type == "name" else "id"
        for field in fields:
            if field[column] == key:
                return field
        # id lookup misses fall back to a name match (ref resolves either key);
        # a total miss is reported in the name flavor, matching ref.
        if column == "id":
            for field in fields:
                if field["name"] == key:
                    return field
            column = "name"
        available = [f[column] for f in fields]
        raise ApiError(
            f'Field "{key}" does not exist in this table',
            HttpErrorCode.NOT_FOUND,
            {
                "domainCode": "field.key_not_found",
                "domainTags": ["not-found"],
                "details": {
                    "fieldKeyType": key_type,
                    "fieldKey": key,
                    "availableFieldKeys": available,
                },
                "localization": {
                    "i18nKey": "httpErrors.field.fieldKeyTypeNotFound",
                    "context": {"fieldKeyType": key_type, "missedFields": key},
                },
            },
        )

    def _project_fields(
        self, fields: list[dict[str, Any]], projection: list[str] | None, key_type: str
    ) -> list[dict[str, Any]]:
        # ref getFieldsByProjection filters the table's fields (definition order)
        # to those the projection names; the client sends field ids, and a
        # name-keyed request also resolves field names. Unknown refs select
        # nothing rather than 404, so an all-unknown projection yields {}.
        if not projection:
            return fields
        wanted = set(projection)
        if key_type == "name":
            return [f for f in fields if f["id"] in wanted or f["name"] in wanted]
        return [f for f in fields if f["id"] in wanted]

    def _view_projected_fields(
        self, fields: list[dict[str, Any]], column_meta_raw: str | None, key_type: str
    ) -> list[dict[str, Any]]:
        # ref getViewProjection: a view with per-column visible/hidden flags
        # narrows the record VO to the shown columns; without either flag the
        # full field set is returned.
        try:
            column_meta = json.loads(column_meta_raw or "{}")
        except (TypeError, ValueError):
            return fields
        if not isinstance(column_meta, dict) or not column_meta:
            return fields
        columns = [c for c in column_meta.values() if isinstance(c, dict)]
        use_visible = any("visible" in c for c in columns)
        use_hidden = any("hidden" in c for c in columns)
        if not use_visible and not use_hidden:
            return fields
        field_by_id = {f["id"]: f for f in fields}
        included: set[str] = set()
        column = "name" if key_type == "name" else "id"
        for field_id, meta in column_meta.items():
            field = field_by_id.get(field_id)
            if field is None or not isinstance(meta, dict):
                continue
            if use_visible:
                if meta.get("visible"):
                    included.add(field[column])
            elif not meta.get("hidden"):
                included.add(field[column])
        if not included:
            return fields
        return [f for f in fields if f[column] in included]

    def _stringify_vo_fields(
        self, vos: list[dict[str, Any]], selected: list[dict[str, Any]], key_type: str
    ) -> None:
        # cellFormat=text renders each present cell through the field's
        # cellValue2String; the record name mirrors the primary cell's text.
        column = "name" if key_type == "name" else "id"
        field_by_key = {f[column]: f for f in selected}
        primary_key = next((f[column] for f in selected if f.get("is_primary")), None)
        for vo in vos:
            cells = vo["fields"]
            for key in list(cells.keys()):
                field = field_by_key.get(key)
                if field is not None:
                    cells[key] = cell_value_to_string(field, cells[key])
            vo["name"] = cells.get(primary_key, "") if primary_key is not None else ""


    @staticmethod
    def _to_db_value(field: dict[str, Any], value: Any) -> Any:
        if value is None:
            return None
        # the reference stores empty text cells as NULL (empty string -> null).
        if value == "" and field["type"] in ("singleLineText", "longText"):
            return None
        # the reference persists unchecked checkbox cells as NULL.
        if field["cell_value_type"] == "boolean" and value is False:
            return None
        if field["type"] in JSONB_FIELD_TYPES:
            return json.dumps(value, separators=(",", ":"))
        if field["cell_value_type"] == "dateTime" and isinstance(value, str):
            # API date cells arrive as ISO strings; the driver wants datetimes.
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _from_db_value(field: dict[str, Any], value: Any) -> Any:
        if value is None:
            return None
        if (field["type"] in JSONB_FIELD_TYPES or field["type"] == "link") and isinstance(
            value, str
        ):
            return json.loads(value)
        if field["cell_value_type"] == "dateTime" and isinstance(value, datetime):
            return _iso(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    @staticmethod
    def _default_cell_value(ftype: str, options: dict[str, Any], dv: Any, user_id: str) -> Any:
        """Port record-modify getDefaultValue: coerce a field's stored
        options.defaultValue into a cell value for a freshly created record."""
        if ftype == "checkbox":
            return True if dv else None
        if ftype == "singleSelect":
            return dv[0] if isinstance(dv, list) else dv
        if ftype == "multipleSelect":
            return dv if isinstance(dv, list) else [dv]
        if ftype == "date":
            return _iso(datetime.now(UTC)) if dv == "now" else dv
        if ftype == "user":
            raw = dv if isinstance(dv, list) else [dv]
            ids = list(dict.fromkeys(user_id if x == "me" else x for x in raw))
            if (options or {}).get("isMultiple"):
                return [{"id": i} for i in ids]
            return {"id": ids[0]} if ids else None
        return dv

    def _with_default_values(
        self,
        fields: list[dict[str, Any]],
        item_fields: dict[str, Any],
        key_type: str,
        user_id: str,
    ) -> dict[str, Any]:
        """appendDefaultValue(onlyMissingFields): fill defaults only for fields
        whose key is absent from the incoming record."""
        result = dict(item_fields)
        for field in fields:
            if field.get("is_computed"):
                continue
            options = json.loads(field.get("options") or "{}")
            if "defaultValue" not in options:
                continue
            dv = options.get("defaultValue")
            if dv is None:
                continue
            key = field["name"] if key_type == "name" else field["id"]
            if key in result:
                continue
            result[key] = self._default_cell_value(field["type"], options, dv, user_id)
        return result

    async def _typecast_cell_value(
        self, table_id: str, field: dict[str, Any], value: Any
    ) -> Any:
        """Coerce a written cell via the paste typecaster when typecast=true
        (string->number, repair checkbox, split/auto-create select, stringify,
        parse date); rating additionally clamps to [1, max]."""
        from ..selection.typecast import FieldTypecaster

        caster = FieldTypecaster({
            "id": field["id"],
            "type": field["type"],
            "options": json.loads(field.get("options") or "{}"),
        })
        cast = caster.cast([value])[0]
        await caster.flush_new_choices(table_id)
        if field["type"] == "rating" and cast is not None:
            mx = int((json.loads(field.get("options") or "{}").get("max")) or 10)
            rounded = min(int(cast + 0.5), mx)
            cast = rounded if rounded >= 1 else None
        return cast

    async def _check_user_cell(
        self, base_id: str, value: Any, cache: dict[str, dict[str, Any]]
    ) -> None:
        """Reject a user cell whose id is not a base collaborator (typecast off).

        Ports UserValueResolverService: an object-shaped user value referencing a
        non-collaborator raises ``User(<id>) not found``; free-form text that
        clears the cell is the typecast path and is handled elsewhere."""
        index = cache.get(base_id)
        if index is None:
            from ..collaborator.service import CollaboratorService

            collaborators = await CollaboratorService().get_user_collaborators(
                base_id, {"take": 1000}
            )
            index = {u["id"]: u for u in collaborators}
            cache[base_id] = index
        for item in value if isinstance(value, list) else [value]:
            uid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(uid, str) or uid not in index:
                raise ApiError(
                    f"User({uid}) not found",
                    HttpErrorCode.VALIDATION_ERROR,
                    {
                        "domainCode": "validation.field.user_not_found",
                        "domainTags": ["validation"],
                        "localization": {"i18nKey": "httpErrors.user.notFound"},
                    },
                )

    async def _resolve_attachment_cell(self, value: Any) -> Any:
        """Resolve a written attachment cell to stored metadata + per-cell ids.

        Ports typecast.validate castToAttachment: each item's token is mapped to
        the stored attachment row and persisted as
        ``{token, path, size, mimetype, width?, height?, name, id}``. An existing
        item id is kept (stable on re-write), duplicates within the cell are
        reissued, and a missing/unknown token errors like the reference. Ephemeral
        signed urls are dropped here — they are rebuilt from stored meta on read.
        """
        if not isinstance(value, list) or not value:
            return value
        from ..attachment import repository as attachment_repository

        tokens = [
            item["token"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("token"), str)
        ]
        meta_map = await attachment_repository.find_attachments_meta_by_tokens(tokens)
        resolved: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in value:
            token = item.get("token") if isinstance(item, dict) else None
            if not isinstance(token, str) or token not in meta_map:
                raise ApiError(
                    f"Attachment({token}) not found",
                    HttpErrorCode.VALIDATION_ERROR,
                    {
                        "domainCode": "validation.field.attachment_not_found",
                        "domainTags": ["validation"],
                    },
                )
            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in seen_ids:
                item_id = new_id(IdPrefix.ATTACHMENT)
            seen_ids.add(item_id)
            merged: dict[str, Any] = {**meta_map[token]}
            name = item.get("name")
            if name is not None:
                merged["name"] = name
            merged["id"] = item_id
            resolved.append(merged)
        return resolved

    async def _presign_attachment_fields(
        self,
        vos: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        key_type: str,
    ) -> None:
        """Decorate attachment cells with ephemeral signed urls on read.

        Ports recordsPresignedUrl: batch the cell tokens, load their thumbnail
        paths, then sign each item's path (and, for images, its thumbnails). The
        signed urls live only in the response — the persisted cell is untouched.
        """
        attachment_fields = [f for f in selected if f["type"] == "attachment"]
        if not attachment_fields:
            return
        key_of = "name" if key_type == "name" else "id"
        keys = [f[key_of] for f in attachment_fields]
        tokens: set[str] = set()
        for vo in vos:
            cells = vo["fields"]
            for key in keys:
                cell = cells.get(key)
                if isinstance(cell, list):
                    for item in cell:
                        if isinstance(item, dict) and isinstance(item.get("token"), str):
                            tokens.add(item["token"])
        if not tokens:
            return
        from ..attachment import repository as attachment_repository
        from ..attachment.service import AttachmentService

        thumb_map = await attachment_repository.find_thumbnail_paths_by_tokens(list(tokens))
        service = AttachmentService()
        for vo in vos:
            cells = vo["fields"]
            for key in keys:
                cell = cells.get(key)
                if not isinstance(cell, list):
                    continue
                cells[key] = [
                    service.presign_cell_item(item, thumb_map.get(item.get("token")))
                    if isinstance(item, dict)
                    else item
                    for item in cell
                ]

    async def _resolve_user_fields(
        self,
        vos: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        key_type: str,
    ) -> None:
        """Enrich user-field cells with the resolved collaborator on read.

        The reference re-resolves user cells to {id, title, email, avatarUrl}
        (refreshed from the user record); wedoc persists only {id, title}, so the
        signed/enriched shape is produced at read time (lookup user fields flow
        through the lookup resolver and are excluded)."""
        user_fields = [f for f in selected if f["type"] == "user" and not f.get("is_lookup")]
        if not user_fields:
            return
        key_of = "name" if key_type == "name" else "id"
        keys = [f[key_of] for f in user_fields]
        ids: set[str] = set()
        for vo in vos:
            cells = vo["fields"]
            for key in keys:
                cell = cells.get(key)
                for item in cell if isinstance(cell, list) else [cell]:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        ids.add(item["id"])
        if not ids:
            return
        users = await repository.get_users_by_ids(list(ids))
        user_cells = {u["id"]: self._user_cell(u) for u in users}

        def _resolve(item: Any) -> Any:
            if isinstance(item, dict) and item.get("id") in user_cells:
                return user_cells[item["id"]]
            return item

        for vo in vos:
            cells = vo["fields"]
            for key in keys:
                cell = cells.get(key)
                if isinstance(cell, list):
                    cells[key] = [_resolve(x) for x in cell]
                elif isinstance(cell, dict):
                    cells[key] = _resolve(cell)

    def _full_vo(
        self, row: dict[str, Any], fields: list[dict[str, Any]], key_type: str = "id"
    ) -> dict[str, Any]:
        key_of = "name" if key_type == "name" else "id"
        values: dict[str, Any] = {}
        name_value: Any = None
        for field in fields:
            # computed cells (formula/lookup/rollup) are authored by the read-time
            # resolvers via _apply_computed_fields; their physical column holds a
            # materialized copy only for filter/sort/group and must not leak here.
            if _is_computed_field(field):
                continue
            value = self._from_db_value(field, row.get(field["db_field_name"]))
            if value is not None:
                values[field[key_of]] = value
            if field.get("is_primary"):
                name_value = value
        vo: dict[str, Any] = {"id": row["__id"], "fields": values}
        vo["name"] = name_value if name_value is not None else ""
        vo["autoNumber"] = row["__auto_number"]
        vo["createdTime"] = _iso(row["__created_time"])
        last_modified_time = row.get("__last_modified_time")
        if last_modified_time is not None:
            vo["lastModifiedTime"] = _iso(last_modified_time)
        vo["createdBy"] = row["__created_by"]
        last_modified_by = row.get("__last_modified_by")
        if last_modified_by is not None:
            vo["lastModifiedBy"] = last_modified_by
        return vo

    async def _apply_computed_fields(
        self,
        all_fields: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        vos: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        key_type: str,
    ) -> None:
        # lookup/rollup values are resolved at read time by aggregating the
        # foreign source field through the link (the reference materializes
        # these asynchronously; wedoc computes them synchronously).
        if not rows:
            return
        key_of = "name" if key_type == "name" else "id"
        await self._apply_system_fields(rows, vos, selected, key_of)
        resolved: dict[str, dict[str, Any]] = {}
        for field in selected:
            if field.get("is_lookup"):
                values = await computed_cells.resolve_lookup(field, all_fields, rows)
            elif field["type"] in ("rollup", "conditionalRollup"):
                values = await computed_cells.resolve_rollup(field, all_fields, rows)
            else:
                continue
            resolved[field["id"]] = values
            for index, row in enumerate(rows):
                value = values.get(row["__id"])
                if value is not None:
                    vos[index]["fields"][field[key_of]] = value
        # formula cells are resolved last: they may reference lookup/rollup or
        # other formula cells, so those inputs must already be resolved.
        formula_selected = [
            f for f in selected if f["type"] == "formula" and not f.get("is_lookup")
        ]
        if formula_selected:
            formula_values = await computed_cells.resolve_formulas(all_fields, rows, resolved)
            for field in formula_selected:
                values = formula_values.get(field["id"], {})
                for index, row in enumerate(rows):
                    value = values.get(row["__id"])
                    if value is not None:
                        vos[index]["fields"][field[key_of]] = value

    async def _materialize_formulas(
        self,
        all_fields: list[dict[str, Any]],
        record_id: str,
        source: dict[str, Any],
        target: dict[str, Any] | None = None,
    ) -> None:
        # a formula reads only its own record, so its cell is materialized from
        # the row being written to give filter/sort/group/aggregation a real
        # column value; the read path still recomputes the VO authoritatively.
        formula_fields = [
            f for f in all_fields if f["type"] == "formula" and not f.get("is_lookup")
        ]
        if not formula_fields:
            return
        target = source if target is None else target
        computed = await computed_cells.resolve_formulas(
            all_fields, [{**source, "__id": record_id}]
        )
        for field in formula_fields:
            value = computed.get(field["id"], {}).get(record_id)
            target[field["db_field_name"]] = self._encode_computed_value(field, value)

    def _encode_computed_value(self, field: dict[str, Any], value: Any) -> Any:
        if value is None:
            return None
        if field.get("is_multiple_cell_value"):
            return json.dumps(value, separators=(",", ":"))
        return self._to_db_value(field, value)

    async def _materialize_computed(
        self,
        table: dict[str, Any],
        all_fields: list[dict[str, Any]],
        record_ids: list[str],
    ) -> None:
        """Persist lookup/rollup/formula cells into their physical columns.

        lookup/rollup aggregate foreign records, so unlike a formula their value
        can go stale when the foreign row changes; this recomputes from the live
        foreign data for the given records so filter/sort/group/aggregation read
        a current value. The read path still recomputes the VO authoritatively.
        """
        lookup_rollup = [
            f
            for f in all_fields
            if f.get("is_lookup") or f["type"] in ("rollup", "conditionalRollup")
        ]
        formula_fields = [
            f for f in all_fields if f["type"] == "formula" and not f.get("is_lookup")
        ]
        if (not lookup_rollup and not formula_fields) or not record_ids:
            return
        columns = [f["db_field_name"] for f in all_fields]
        rows: list[dict[str, Any]] = []
        for record_id in dict.fromkeys(record_ids):
            row = await repository.fetch_row(table["base_id"], table["id"], record_id, columns)
            if row is not None:
                rows.append(row)
        if not rows:
            return
        resolved: dict[str, dict[str, Any]] = {}
        for field in lookup_rollup:
            if field.get("is_lookup"):
                resolved[field["id"]] = await computed_cells.resolve_lookup(
                    field, all_fields, rows
                )
            else:
                resolved[field["id"]] = await computed_cells.resolve_rollup(
                    field, all_fields, rows
                )
        formula_values: dict[str, dict[str, Any]] = {}
        if formula_fields:
            formula_values = await computed_cells.resolve_formulas(all_fields, rows, resolved)
        for row in rows:
            record_id = row["__id"]
            updates: dict[str, Any] = {}
            for field in lookup_rollup:
                updates[field["db_field_name"]] = self._encode_computed_value(
                    field, resolved[field["id"]].get(record_id)
                )
            for field in formula_fields:
                updates[field["db_field_name"]] = self._encode_computed_value(
                    field, formula_values.get(field["id"], {}).get(record_id)
                )
            await repository.set_computed_columns(
                table["base_id"], table["id"], record_id, updates
            )

    async def _recompute_dependents(
        self,
        table_id: str,
        changed_ids: list[str],
        _depth: int = 0,
        _visited: frozenset[str] | None = None,
    ) -> None:
        """Recompute lookup/rollup that aggregate records changed in ``table_id``.

        A write to the foreign (source) table shifts every host record whose link
        cell references a changed row; those host cells are re-materialized so a
        later filter/sort/group/aggregation on them reflects the new value. Bounded
        recursion follows chained lookups; a visited guard breaks link cycles.
        """
        if not changed_ids or _depth > 4:
            return
        visited = _visited or frozenset()
        if table_id in visited:
            return
        visited = visited | {table_id}
        referencing = await field_repository.list_fields_referencing_foreign_table(table_id)
        if not referencing:
            return
        by_table: dict[str, list[dict[str, Any]]] = {}
        for field in referencing:
            by_table.setdefault(field["table_id"], []).append(field)
        for host_table_id, ref_fields in by_table.items():
            host_table = await get_table_meta_by_id(host_table_id)
            if host_table is None or host_table["deleted_time"] is not None:
                continue
            host_fields = await table_repository.list_field_rows(host_table_id)
            host_by_id = {f["id"]: f for f in host_fields}
            link_field_ids = {
                json.loads(f["lookup_options"] or "{}").get("linkFieldId") for f in ref_fields
            }
            affected: set[str] = set()
            for link_field_id in link_field_ids:
                link_field = host_by_id.get(link_field_id)
                if link_field is None:
                    continue
                ids = await repository.list_ids_linking_to(
                    host_table["base_id"],
                    host_table_id,
                    link_field["db_field_name"],
                    list(changed_ids),
                    bool(link_field.get("is_multiple_cell_value")),
                )
                affected.update(ids)
            if not affected:
                continue
            await self._materialize_computed(host_table, host_fields, list(affected))
            await self._recompute_dependents(host_table_id, list(affected), _depth + 1, visited)

    async def _apply_system_fields(
        self,
        rows: list[dict[str, Any]],
        vos: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        key_of: str,
    ) -> None:
        # createdTime/lastModifiedTime/createdBy/lastModifiedBy/autoNumber cells
        # derive from the row's system columns; the per-field physical column is
        # never populated. Lookup variants of these types flow through
        # resolve_lookup instead, so they are excluded here.
        system_fields = [
            f
            for f in selected
            if not f.get("is_lookup") and f["type"] in SYSTEM_COMPUTED_FIELD_TYPES
        ]
        if not system_fields:
            return
        user_cells: dict[str, dict[str, Any]] = {}
        if any(f["type"] in ("createdBy", "lastModifiedBy") for f in system_fields):
            user_ids = {
                uid
                for row in rows
                for uid in (row.get("__created_by"), row.get("__last_modified_by"))
                if uid
            }
            if user_ids:
                users = await repository.get_users_by_ids(list(user_ids))
                user_cells = {u["id"]: self._user_cell(u) for u in users}
        for field in system_fields:
            field_type = field["type"]
            key = field[key_of]
            for index, row in enumerate(rows):
                value = self._system_field_value(field_type, row, user_cells)
                if value is not None:
                    vos[index]["fields"][key] = value

    def _system_field_value(
        self,
        field_type: str,
        row: dict[str, Any],
        user_cells: dict[str, dict[str, Any]],
    ) -> Any:
        if field_type == "createdTime":
            return _iso(row["__created_time"])
        if field_type == "lastModifiedTime":
            return _iso(row.get("__last_modified_time"))
        if field_type == "autoNumber":
            return row["__auto_number"]
        if field_type == "createdBy":
            return self._audit_user_cell(row.get("__created_by"), user_cells)
        if field_type == "lastModifiedBy":
            if row.get("__last_modified_time") is None:
                return None
            return self._audit_user_cell(row.get("__last_modified_by"), user_cells)
        return None

    @staticmethod
    def _user_cell(user: dict[str, Any]) -> dict[str, Any]:
        cell: dict[str, Any] = {
            "id": user["id"],
            "title": user["name"],
            "email": user["email"],
        }
        if user.get("avatar"):
            cell["avatarUrl"] = get_public_full_storage_url(user["avatar"])
        return cell

    @staticmethod
    def _audit_user_cell(
        user_id: str | None, user_cells: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not user_id:
            return None
        return user_cells.get(user_id) or {"id": user_id, "title": user_id}

    @staticmethod
    def _delete_echo(
        record_id: str, row: dict[str, Any], fields: list[dict[str, Any]]
    ) -> dict[str, Any]:
        # delete/duplicate echoes serialize every field, nulls included.
        values = {
            field["id"]: RecordService._from_db_value(field, row.get(field["db_field_name"]))
            for field in fields
        }
        return {"id": record_id, "fields": values}

    @staticmethod
    def _mutation_echo(vo: dict[str, Any]) -> dict[str, Any]:
        # ref's write endpoints (create/update/delete/duplicate/formSubmit) map
        # the result through the v2 record DTO, which is just {id, fields}; the
        # fields map still carries the fully-computed cells. Reads (getRecord/
        # getRecords) keep the full VO, so trim only the mutation return value.
        return {"id": vo["id"], "fields": vo["fields"]}

    @staticmethod
    def _delete_echo_vo(vo: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
        # unlike create/get/list (which omit empty cells), the ref's delete echo
        # carries an entry for every field — computed included — with null for
        # empty cells; the delete VO is built id-keyed.
        cells = vo["fields"]
        return {"id": vo["id"], "fields": {f["id"]: cells.get(f["id"]) for f in fields}}

    @staticmethod
    def _create_audit_stamp(user_id: str) -> dict[str, Any]:
        # ref stamps last-modified at creation equal to the created values, so a
        # freshly created record reads back with lastModifiedTime == createdTime
        # and lastModifiedBy == createdBy (and the lastModified* system-field
        # cells resolve instead of staying null). __created_time is written
        # explicitly with the same value rather than left to the column's now()
        # default, guaranteeing the two timestamps are exactly equal.
        now = datetime.now(UTC).replace(tzinfo=None)
        return {
            "__created_time": now,
            "__last_modified_time": now,
            "__last_modified_by": user_id,
        }

    # ---- query compilation -------------------------------------------------

    def _resolve_field(
        self, fields: list[dict[str, Any]], key: str
    ) -> dict[str, Any] | None:
        # filter/sort field refs accept id or name; unknown refs are dropped
        # silently by the reference query builder (match-all), never 404.
        for field in fields:
            if field["id"] == key or field["name"] == key:
                return field
        return None

    def _compile_filter(
        self,
        fields: list[dict[str, Any]],
        filter_obj: dict[str, Any] | None,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        if not filter_obj:
            return ""
        conjunction = filter_obj.get("conjunction") or "and"
        joiner = " AND " if conjunction == "and" else " OR "
        clauses = []
        for item in filter_obj.get("filterSet") or []:
            if "filterSet" in item:
                nested = self._compile_filter(fields, item, params, counter)
                if nested:
                    clauses.append(f"({nested})")
                continue
            operator = item.get("operator")
            field = self._resolve_field(fields, item.get("fieldId", ""))
            is_link = field is not None and field["type"] == "link"
            is_multi_select = field is not None and field["type"] == "multipleSelect"
            is_single_select = (
                field is not None
                and field["type"] == "singleSelect"
                and not field.get("is_multiple_cell_value")
            )
            is_user_single = (
                field is not None
                and field["type"] == "user"
                and not field.get("is_multiple_cell_value")
            )
            is_multi_user = (
                field is not None
                and field["type"] == "user"
                and bool(field.get("is_multiple_cell_value"))
            )
            is_system_user = (
                field is not None and field["type"] in _SYSTEM_USER_FILTER_COLUMN
            )
            if field is None:
                # a missing field's condition is ignored (not a 400), matching
                # the reference's FIELD_NOT_FOUND skip.
                continue
            if operator not in _valid_filter_operators(field):
                # getValidFilterOperators: an operator outside the field type's
                # allow-list is a validation error (also stops type-mismatched
                # operators reaching the SQL layer and crashing).
                raise _invalid_filter_operator()
            column = f'"{field["db_field_name"]}"'
            if field["type"] in _SYSTEM_DATE_FILTER_COLUMN:
                # createdTime/lastModifiedTime read from a system timestamptz
                # column; redirect so the dateTime range filter hits real data.
                column = f'"{_SYSTEM_DATE_FILTER_COLUMN[field["type"]]}"'
            value = item.get("value")
            if is_system_user:
                # createdBy/lastModifiedBy resolve from a system column holding the
                # bare user id; compare as string (string adapter semantics).
                sys_col = f'"{_SYSTEM_USER_FILTER_COLUMN[field["type"]]}"'
                clause = self._system_user_filter_condition(
                    sys_col, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if is_link:
                if operator == "isEmpty" or (operator == "is" and value is None):
                    clauses.append(f"{column} IS NULL")
                    continue
                if operator == "isNotEmpty" or (operator == "isNot" and value is None):
                    clauses.append(f"{column} IS NOT NULL")
                    continue
                clause = self._link_filter_condition(
                    column, field, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if is_multi_user:
                # multi-value user shares the multiple-json adapter with link:
                # id-set membership (hasAnyOf/hasAllOf/hasNoneOf) and set equality
                # (isExactly/isNotExactly) over `$[*].id`; empty is null-only.
                if operator == "isEmpty":
                    clauses.append(f"{column} IS NULL")
                    continue
                if operator == "isNotEmpty":
                    clauses.append(f"{column} IS NOT NULL")
                    continue
                clause = self._link_filter_condition(
                    column, field, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if is_multi_select:
                clause = self._multi_select_filter_condition(
                    column, field, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if is_single_select and operator in ("isAnyOf", "isNoneOf"):
                # single-select supports isAnyOf ($in) / isNoneOf ($notIn): the
                # string adapter compiles `col IN (..)` and `COALESCE(col,'')
                # NOT IN (..)` (the latter keeps null/empty cells).
                clause = self._single_select_in_condition(
                    column, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if is_user_single and operator in ("is", "isNot", "isAnyOf", "isNoneOf") and (
                value is not None
            ):
                # single user cell is jsonb {id,...}; the json adapter compares on
                # the extracted id (is/isAnyOf strict, isNot/isNoneOf keep nulls).
                clause = self._user_filter_condition(
                    column, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if field["cell_value_type"] == "boolean" and operator == "is":
                # checkbox/boolean: truthy -> `= true`; falsy -> `(= false OR
                # null)` so unchecked (null) and formula-false cells both match.
                if value:
                    clauses.append(f"{column} = true")
                else:
                    clauses.append(f"({column} = false OR {column} IS NULL)")
                continue
            if field["cell_value_type"] == "dateTime":
                # date/createdTime/lastModifiedTime and dateTime formula/rollup
                # cells filter by a mode-resolved [start, end] range, not the
                # scalar operators; handled before the enum drop so isWithIn /
                # isBefore / isAfter / isOnOr* are not silently discarded.
                clause = self._date_filter_condition(
                    column, field, operator, value, params, counter
                )
                if clause:
                    clauses.append(clause)
                continue
            if operator not in _FILTER_ENUM:
                continue
            if operator == "isEmpty" or (operator == "is" and value is None):
                if field["cell_value_type"] == "string":
                    clauses.append(f"({column} IS NULL OR {column} = '')")
                else:
                    clauses.append(f"{column} IS NULL")
                continue
            if operator == "isNotEmpty" or (operator == "isNot" and value is None):
                if field["cell_value_type"] == "string":
                    clauses.append(f"({column} IS NOT NULL AND {column} != '')")
                else:
                    clauses.append(f"{column} IS NOT NULL")
                continue
            template = FILTER_OPERATORS[operator]
            if isinstance(value, str) and operator in ("contains", "doesNotContain"):
                value = f"%{value}%"
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = self._to_db_value(field, value)
            clauses.append(f"{column} {template.format(p=param)}")
        return joiner.join(clauses)

    def _single_select_in_condition(
        self,
        column: str,
        operator: str,
        value: Any,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        if not values:
            # empty set: isAnyOf matches nothing, isNoneOf matches everything.
            return "1 = 0" if operator == "isAnyOf" else "1 = 1"
        placeholders = []
        for item in values:
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = item
            placeholders.append(f":{param}")
        joined = ", ".join(placeholders)
        if operator == "isAnyOf":
            return f"{column} IN ({joined})"
        return f"COALESCE({column}, '') NOT IN ({joined})"

    def _user_filter_condition(
        self,
        column: str,
        operator: str,
        value: Any,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        # JsonCellValueFilterAdapter (single-value user): compare on the id
        # extracted from the jsonb cell.
        extract = f"jsonb_extract_path_text({column}::jsonb, 'id')"
        extract_null = (
            f"jsonb_extract_path_text(COALESCE({column}, '{{}}')::jsonb, 'id')"
        )
        if operator in ("is", "isNot"):
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = value
            if operator == "is":
                return f"{extract} = :{param}"
            return f"{extract_null} IS DISTINCT FROM :{param}"
        values = value if isinstance(value, list) else [value]
        if not values:
            return "1 = 0" if operator == "isAnyOf" else "1 = 1"
        placeholders = []
        for item in values:
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = item
            placeholders.append(f":{param}")
        joined = ", ".join(placeholders)
        if operator == "isAnyOf":
            return f"{extract} IN ({joined})"
        return f"COALESCE({extract_null}, '') NOT IN ({joined})"

    def _system_user_filter_condition(
        self,
        column: str,
        operator: str,
        value: Any,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        # createdBy/lastModifiedBy system column stores the bare user id; the
        # string adapter compares it directly (is/isNot/isAnyOf/isNoneOf) with
        # null-based emptiness.
        if operator == "isEmpty" or (operator == "is" and value is None):
            return f"{column} IS NULL"
        if operator == "isNotEmpty" or (operator == "isNot" and value is None):
            return f"{column} IS NOT NULL"
        if operator in ("is", "isNot"):
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = str(value)
            if operator == "is":
                return f"{column} = :{param}"
            return f"{column} IS DISTINCT FROM :{param}"
        if operator in ("isAnyOf", "isNoneOf"):
            return self._single_select_in_condition(column, operator, value, params, counter)
        return ""

    def _date_filter_condition(
        self,
        column: str,
        field: dict[str, Any],
        operator: str,
        value: Any,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        # ports the postgres single-value datetime cell-value filter adapter:
        # a {mode,...} value resolves to a [start, end] timestamptz range and the
        # operator maps to a BETWEEN / boundary comparison. Range endpoints bind
        # as tz-aware datetimes (the physical column is timestamptz).
        if operator == "isEmpty" or (operator == "is" and value is None):
            return f"{column} IS NULL"
        if operator == "isNotEmpty" or (operator == "isNot" and value is None):
            return f"{column} IS NOT NULL"
        if not isinstance(value, dict) or "mode" not in value:
            # field-reference ({type:'field'}) / resolved-literal date comparisons
            # are not compiled here; drop the predicate rather than mis-filter.
            return ""
        start, end = _date_filter_range(value, _field_options(field))

        def bind(val: datetime) -> str:
            name = f"f{counter[0]}"
            counter[0] += 1
            params[name] = val
            return name

        op = _DATE_OP_ALIAS.get(operator, operator)
        if op in ("is", "isWithIn"):
            return f"{column} BETWEEN :{bind(start)} AND :{bind(end)}"
        if op == "isNot":
            lo, hi = bind(start), bind(end)
            return f"({column} NOT BETWEEN :{lo} AND :{hi} OR {column} IS NULL)"
        if op == "isAfter":
            return f"{column} > :{bind(end)}"
        if op == "isOnOrAfter":
            return f"{column} >= :{bind(start)}"
        if op == "isBefore":
            return f"{column} < :{bind(start)}"
        if op == "isOnOrBefore":
            return f"{column} <= :{bind(end)}"
        return ""

    def _link_filter_condition(
        self,
        column: str,
        field: dict[str, Any],
        operator: str,
        value: Any,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        # ports the postgres json/multiple-json link filter adapters: id-set
        # membership for is/isAnyOf/hasAllOf/isExactly, title regex for contains.
        def bind(val: Any) -> str:
            name = f"f{counter[0]}"
            counter[0] += 1
            params[name] = val
            return name

        def id_array(val: Any) -> str:
            items = val if isinstance(val, list) else [val]
            ids = [i for i in items if isinstance(i, str)]
            if not ids:
                return "ARRAY[]::text[]"
            placeholders = ", ".join(f":{bind(i)}" for i in ids)
            return f"ARRAY[{placeholders}]::text[]"

        alias = {"hasAnyOf": "isAnyOf", "hasNoneOf": "isNoneOf"}
        operator = alias.get(operator, operator)
        multiple = bool(field.get("is_multiple_cell_value"))

        if operator == "contains":
            path = "$[*].title" if multiple else "$.title"
            jp = f'{path} ? (@ like_regex "{_jsonpath_escape(str(value))}" flag "i")'
            col = f"COALESCE({column},'[]')::jsonb" if multiple else f"{column}::jsonb"
            return f"jsonb_path_exists({col}, CAST(:{bind(jp)} AS jsonpath))"
        if operator == "doesNotContain":
            path = "$[*].title" if multiple else "$.title"
            jp = f'{path} ? (@ like_regex "{_jsonpath_escape(str(value))}" flag "i")'
            fallback = "'[]'" if multiple else "'{}'"
            return (
                f"NOT jsonb_path_exists(COALESCE({column},{fallback})::jsonb, "
                f"CAST(:{bind(jp)} AS jsonpath))"
            )

        if multiple:
            arr = f"jsonb_path_query_array({column}::jsonb,'$[*].id')"
            arr_c = f"jsonb_path_query_array(COALESCE({column},'[]')::jsonb,'$[*].id')"
            if operator in ("is", "isAnyOf"):
                return f"jsonb_exists_any({arr}, {id_array(value)})"
            if operator in ("isNot", "isNoneOf"):
                return f"NOT jsonb_exists_any({arr_c}, {id_array(value)})"
            if operator == "hasAllOf":
                return f"jsonb_exists_all({arr}, {id_array(value)})"
            if operator == "isExactly":
                arr_sql = id_array(value)
                return f"{arr} @> to_jsonb({arr_sql}) AND to_jsonb({arr_sql}) @> {arr}"
            if operator == "isNotExactly":
                arr_sql = id_array(value)
                return (
                    f"(NOT ({arr_c} @> to_jsonb({arr_sql}) "
                    f"AND to_jsonb({arr_sql}) @> {arr_c}) OR {column} IS NULL)"
                )
            return ""

        # single-value link
        id_expr = f"jsonb_extract_path_text({column}::jsonb, 'id')"
        if operator == "is":
            return f"{id_expr} = :{bind(str(value))}"
        if operator == "isNot":
            return (
                f"jsonb_extract_path_text(COALESCE({column},'{{}}'::jsonb), 'id') "
                f"IS DISTINCT FROM :{bind(str(value))}"
            )
        if operator == "isAnyOf":
            return f"{id_expr} = ANY({id_array(value)})"
        if operator == "isNoneOf":
            return (
                f"COALESCE(jsonb_extract_path_text(COALESCE({column},'{{}}')::jsonb,'id'),'') "
                f"<> ALL({id_array(value)})"
            )
        return ""

    def _multi_select_filter_condition(
        self,
        column: str,
        field: dict[str, Any],
        operator: str,
        value: Any,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        # multipleSelect cells are a jsonb array of choice-name strings. Ports the
        # postgres multiple-value select adapters: membership via jsonb_exists_*,
        # set equality via @>. Operators the reference rejects for a multi-value
        # select (is/isNot/contains/doesNotContain/isAnyOf/isNoneOf) error here too.
        def bind(val: Any) -> str:
            name = f"f{counter[0]}"
            counter[0] += 1
            params[name] = val
            return name

        def name_array(val: Any) -> str:
            items = val if isinstance(val, list) else [val]
            names = [i for i in items if isinstance(i, str)]
            if not names:
                return "ARRAY[]::text[]"
            placeholders = ", ".join(f":{bind(n)}" for n in names)
            return f"ARRAY[{placeholders}]::text[]"

        if operator == "isEmpty":
            return f"({column} IS NULL OR jsonb_array_length({column}::jsonb) = 0)"
        if operator == "isNotEmpty":
            return f"({column} IS NOT NULL AND jsonb_array_length({column}::jsonb) > 0)"

        arr = f"COALESCE({column},'[]')::jsonb"
        if operator == "hasAnyOf":
            return f"jsonb_exists_any({arr}, {name_array(value)})"
        if operator == "hasNoneOf":
            return f"NOT jsonb_exists_any({arr}, {name_array(value)})"
        if operator == "hasAllOf":
            return f"jsonb_exists_all({arr}, {name_array(value)})"
        if operator == "isExactly":
            vj = f"to_jsonb({name_array(value)})"
            return f"({arr} @> {vj} AND {vj} @> {arr})"
        if operator == "isNotExactly":
            vj = f"to_jsonb({name_array(value)})"
            return f"(NOT ({arr} @> {vj} AND {vj} @> {arr}) OR {column} IS NULL)"
        raise _invalid_filter_operator()

    def _compile_sort(
        self, fields: list[dict[str, Any]], sort_items: list[dict[str, Any]] | None
    ) -> str:
        if not sort_items:
            return ""
        terms = []
        for item in sort_items:
            field = self._resolve_field(fields, item.get("fieldId", ""))
            if field is None:
                continue
            direction = "DESC" if item.get("order") == "desc" else "ASC"
            # the reference sort function pins NULLS FIRST on asc / NULLS LAST
            # on desc (opposite of the PG default).
            nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
            column = f'"{field["db_field_name"]}"'
            if (
                field["type"] == "singleSelect"
                and not field.get("is_multiple_cell_value")
                and not field.get("is_lookup")
            ):
                # single-select sorts by the choice's position in options.choices
                # (definition order), not alphabetically. array_position yields
                # null for null/unknown cells so NULLS FIRST/LAST still applies.
                names = [
                    ch.get("name")
                    for ch in (_field_options(field).get("choices") or [])
                    if isinstance(ch, dict) and ch.get("name") is not None
                ]
                if names:
                    arr = ", ".join("'" + n.replace("'", "''") + "'" for n in names)
                    expr = f"array_position(ARRAY[{arr}]::text[], {column})"
                    terms.append(f"{expr} {direction} {nulls}")
                    continue
            if field["type"] == "multipleSelect" and not field.get("is_lookup"):
                # multi-select sorts by the array of choice indices compared
                # lexicographically (v2 select ordering): [Z] < [Z,M] < [M] < [A].
                names = [
                    ch.get("name")
                    for ch in (_field_options(field).get("choices") or [])
                    if isinstance(ch, dict) and ch.get("name") is not None
                ]
                if names:
                    arr = "ARRAY[" + ", ".join(
                        "'" + n.replace("'", "''") + "'" for n in names
                    ) + "]::text[]"
                    expr = (
                        f"(SELECT array_agg(array_position({arr}, elem) ORDER BY ord) "
                        f"FROM jsonb_array_elements_text({column}::jsonb) "
                        f"WITH ORDINALITY AS t(elem, ord))"
                    )
                    terms.append(f"{expr} {direction} {nulls}")
                    continue
            if field["type"] == "link":
                # link sorts by title(s): single ->> 'title', multi -> joined titles.
                if field.get("is_multiple_cell_value"):
                    expr = f"jsonb_path_query_array({column}::jsonb,'$[*].title')::text"
                else:
                    expr = f"{column}::jsonb ->> 'title'"
                terms.append(f"{expr} {direction} {nulls}")
                continue
            terms.append(f"{column} {direction} {nulls}")
        return " ORDER BY " + ", ".join(terms) if terms else ""

    # ---- endpoints ---------------------------------------------------------

    async def get_record(
        self,
        table_id: str,
        record_id: str,
        field_key_type: str,
        projection: list[str] | None = None,
        cell_format: str = "json",
    ) -> dict[str, Any]:
        _require_record_id(record_id)
        table, fields = await self._load_context(table_id)
        row = await repository.fetch_row(
            table["base_id"], table_id, record_id, [f["db_field_name"] for f in fields]
        )
        if row is None:
            raise _record_not_found(localization=True)
        selected = self._project_fields(fields, projection, field_key_type)
        vo = self._full_vo(row, selected, field_key_type)
        await self._apply_computed_fields(fields, [row], [vo], selected, field_key_type)
        await self._presign_attachment_fields([vo], selected, field_key_type)
        await self._resolve_user_fields([vo], selected, field_key_type)
        if cell_format == "text":
            self._stringify_vo_fields([vo], selected, field_key_type)
        return vo

    async def list_records(
        self,
        table_id: str,
        field_key_type: str = "id",
        projection: list[str] | None = None,
        view_id: str | None = None,
        filter_param: dict[str, Any] | None = None,
        sort_param: list[dict[str, Any]] | None = None,
        order_by: list[dict[str, Any]] | None = None,
        group_by: list[dict[str, Any]] | None = None,
        collapsed_group_ids: list[str] | None = None,
        tql: str | None = None,
        search: list[Any] | None = None,
        ignore_view_query: bool = False,
        take: int = DEFAULT_TAKE,
        skip: int = 0,
        cursor: str | None = None,
        cell_format: str = "json",
    ) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)

        # search is the parsed [value, field?, isExact?] tuple (1-3 items) or None;
        # the router rejects a scalar `search=x` before we get here (qs semantics).
        search_filter: dict[str, Any] | None = None
        if search is not None and len(search) == 3 and search[2] in ("true", True):
            # exact search compiles to a plain equality predicate on the field;
            # an unknown field silently drops the predicate (match-all).
            if self._resolve_field(fields, str(search[1])) is not None:
                search_filter = {
                    "conjunction": "and",
                    "filterSet": [
                        {"fieldId": search[1], "operator": "is", "value": search[0]}
                    ],
                }
        # the non-exact branch searches an index the reference build never
        # populates, so it matches every row: no predicate is added.

        view_filter: dict[str, Any] | None = None
        view_sort: list[dict[str, Any]] | None = None
        view_column_meta: str | None = None
        if view_id:
            view = await get_view_row(table_id, view_id)
            if view is None:
                raise ApiError(
                    f"View {view_id} not found",
                    HttpErrorCode.NOT_FOUND,
                    {"localization": {"i18nKey": "httpErrors.view.notFound"}},
                )
            # column visibility applies to the projection even under
            # ignoreViewQuery, which only relaxes the view filter/sort.
            view_column_meta = view.get("column_meta")
            if not ignore_view_query:
                view_filter = json.loads(view["filter"]) if view.get("filter") else None
                view_sort = json.loads(view["sort"])["sortObjs"] if view.get("sort") else None

        # filterByTql overwrites the filter param (ref TqlPipe assigns it).
        query_filter = filter_param
        if tql:
            try:
                query_filter = parse_tql(tql)
            except TqlParseError as exc:
                raise ApiError(
                    f"TQL parse error, {exc}",
                    HttpErrorCode.VALIDATION_ERROR,
                ) from None

        filter_parts = []
        if view_filter:
            filter_parts.append(view_filter)
        if query_filter:
            filter_parts.append(query_filter)
        if search_filter:
            filter_parts.append(search_filter)
        if len(filter_parts) > 1:
            filter_obj: dict[str, Any] | None = {
                "conjunction": "and",
                "filterSet": filter_parts,
            }
        else:
            filter_obj = filter_parts[0] if filter_parts else None

        if projection:
            selected = self._project_fields(fields, projection, field_key_type)
        elif view_id:
            selected = self._view_projected_fields(fields, view_column_meta, field_key_type)
        else:
            selected = fields

        params: dict[str, Any] = {}
        counter = [0]
        clauses = []
        compiled = self._compile_filter(fields, filter_obj, params, counter)
        if compiled:
            clauses.append(compiled)
        # group points aggregate over the full filtered set (no cursor page).
        group_where = (" WHERE " + compiled) if compiled else ""
        group_params = dict(params)
        if cursor:
            clauses.append("__auto_number > :cursor")
            params["cursor"] = int(cursor)
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        # the legacy `sort` param is ignored by ref; `orderBy` overrides the
        # view's sort, which in turn overrides the default auto-number order.
        # groupBy always sorts first (records are returned in group order).
        group_items = []
        for item in group_by or []:
            if self._resolve_field(fields, item.get("fieldId", "")) is not None:
                group_items.append(
                    {"fieldId": item.get("fieldId", ""), "order": item.get("order", "asc")}
                )
        base_sort = order_by if order_by else view_sort
        order_sql = self._compile_sort(fields, group_items + (base_sort or []))

        # with no active sort/group, a view orders by its manual row-order column
        # (created lazily on first reorder); absent that, by insertion order.
        default_order = ' ORDER BY "__auto_number" ASC'
        if not order_sql and view_id:
            row_col = f"__row_{view_id}"
            if await repository.column_exists(table["base_id"], table_id, row_col):
                default_order = f' ORDER BY "{row_col}" ASC, "__auto_number" ASC'

        rows = await repository.list_rows(
            table["base_id"],
            table_id,
            [f["db_field_name"] for f in fields],
            where_sql=where_sql,
            params=params,
            order_sql=order_sql or default_order,
            limit=take + 1,
            offset=skip,
        )
        result: dict[str, Any] = {}
        if len(rows) > take:
            rows = rows[:take]
            result["extra"] = {"nextCursor": str(rows[-1]["__auto_number"])}
        vos = [self._full_vo(r, selected, field_key_type) for r in rows]
        await self._apply_computed_fields(fields, rows, vos, selected, field_key_type)
        await self._presign_attachment_fields(vos, selected, field_key_type)
        await self._resolve_user_fields(vos, selected, field_key_type)
        if cell_format == "text":
            self._stringify_vo_fields(vos, selected, field_key_type)
        result["records"] = vos
        if search is not None and len(search) >= 2:
            # search never removes rows (exact search already filtered above);
            # it only annotates the page with the cells that matched.
            hit_field = self._resolve_field(fields, str(search[1]))
            hits: list[dict[str, Any]] = []
            if hit_field is not None:
                column = hit_field["db_field_name"]
                exact = len(search) == 3 and search[2] in ("true", True)
                needle = str(search[0])
                for row in rows:
                    raw = row.get(column)
                    if raw is None:
                        continue
                    matched = str(raw) == needle if exact else needle.lower() in str(raw).lower()
                    if matched:
                        hits.append({"fieldId": hit_field["id"], "recordId": row["__id"]})
            result.setdefault("extra", {})["searchHitIndex"] = hits
        if group_items:
            # extra.groupPoints / allGroupHeaderRefs mirror getGroupRelatedData;
            # searchHitIndex is emitted as null when no search accompanies group.
            from ..aggregation.service import AggregationService

            group_result = await AggregationService().group_points_for_records(
                table, fields, group_by, group_where, group_params, collapsed_group_ids
            )
            if group_result is not None:
                points, refs = group_result
                extra = result.setdefault("extra", {})
                extra.setdefault("searchHitIndex", None)
                extra["groupPoints"] = points
                extra["allGroupHeaderRefs"] = refs
        return result

    # ---- undo/redo operation capture ---------------------------------------

    def _cell_contexts(
        self,
        record_id: str,
        changed_fields: list[dict[str, Any]],
        old_row: dict[str, Any],
        new_row: dict[str, Any],
    ) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        for field in changed_fields:
            column = field["db_field_name"]
            new_value = self._from_db_value(field, new_row.get(column))
            old_value = self._from_db_value(field, old_row.get(column))
            if new_value == old_value:
                continue
            contexts.append(
                {
                    "recordId": record_id,
                    "fieldId": field["id"],
                    "oldValue": old_value,
                    "newValue": new_value,
                }
            )
        return contexts

    async def _push_update_op(
        self,
        table_id: str,
        record_ids: list[str],
        changed_fields: list[dict[str, Any]],
        cell_contexts: list[dict[str, Any]],
    ) -> None:
        if not cell_contexts:
            return
        from ..undo_redo.stack import capture_operation

        operation = {
            "name": "updateRecords",
            "params": {
                "tableId": table_id,
                "recordIds": record_ids,
                "fieldIds": [f["id"] for f in changed_fields],
            },
            "result": {"cellContexts": cell_contexts},
        }
        await capture_operation(table_id, operation)

    async def _push_create_op(self, table_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        from ..undo_redo.stack import capture_operation

        operation = {
            "name": "createRecords",
            "params": {"tableId": table_id},
            "result": {"records": records},
        }
        await capture_operation(table_id, operation)

    async def _push_delete_op(self, table_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        from ..undo_redo.stack import capture_operation

        operation = {
            "name": "deleteRecords",
            "params": {"tableId": table_id},
            "result": {"records": records},
        }
        await capture_operation(table_id, operation)

    async def restore_records(
        self, table_id: str, records: list[dict[str, Any]]
    ) -> None:
        """Re-insert deleted records with their original ids (undo of delete).

        The reference restores from the record_trash snapshot, preserving the
        record's original createdTime/createdBy and lastModified* rather than
        stamping fresh audit columns."""
        table, fields = await self._load_context(table_id)
        by_id = {f["id"]: f for f in fields}
        user_id = cls.get("user.id")
        from ..trash import repository as trash_repository

        record_ids = [record["id"] for record in records]
        snap_by_id: dict[str, dict[str, Any]] = {}
        for row in await trash_repository.list_record_trash(table_id, record_ids):
            snap_by_id.setdefault(row["recordId"], json.loads(row["snapshot"] or "{}"))
        for record in records:
            record_id = record["id"]
            snap = snap_by_id.get(record_id, {})
            values: dict[str, Any] = {
                "__id": record_id,
                "__created_by": snap.get("createdBy") or user_id,
                "__version": 1,
            }
            created_time = _parse_iso(snap.get("createdTime"))
            if created_time is not None:
                values["__created_time"] = created_time
            last_modified_time = _parse_iso(snap.get("lastModifiedTime"))
            if last_modified_time is not None:
                values["__last_modified_time"] = last_modified_time
            if snap.get("lastModifiedBy"):
                values["__last_modified_by"] = snap["lastModifiedBy"]
            for field_id, value in (record.get("fields") or {}).items():
                field = by_id.get(field_id)
                if field is None or value is None:
                    continue
                values[field["db_field_name"]] = self._to_db_value(field, value)
            await self._materialize_formulas(fields, record_id, values)
            try:
                await repository.insert_row(table["base_id"], table_id, values)
            except IntegrityError as exc:
                err = _unique_violation_error(exc, fields, "insert")
                if err is not None:
                    raise err from exc
                raise
            from ...realtime.broadcast import broadcast_record_create

            await broadcast_record_create(
                table_id, record_id, {"id": record_id, "fields": {}}
            )
        restored_ids = [record["id"] for record in records]
        await self._materialize_computed(table, fields, restored_ids)
        await self._recompute_dependents(table_id, restored_ids)

    async def _broadcast_edit(
        self,
        table_id: str,
        record_id: str,
        changed_fields: list[dict[str, Any]],
        old_row: dict[str, Any],
        new_row: dict[str, Any],
    ) -> None:
        from ...realtime.broadcast import broadcast_record_edit, build_set_record_op

        ops = []
        for field in changed_fields:
            column = field["db_field_name"]
            new_value = self._from_db_value(field, new_row.get(column))
            old_value = self._from_db_value(field, old_row.get(column))
            if new_value == old_value:
                continue
            ops.append(build_set_record_op(field["id"], new_value, old_value))
        version = new_row.get("__version") or 1
        await broadcast_record_edit(table_id, record_id, ops, int(version))

    # ---- realtime socket snapshots -----------------------------------------

    async def socket_snapshot_bulk(
        self,
        table_id: str,
        ids: list[str],
        projection: dict[str, bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ShareDB record snapshots ``{id, v, type, data}`` for ids."""
        if not ids:
            return []
        table, fields = await self._load_context(table_id)
        selected = fields
        if projection:
            wanted = {k for k, v in projection.items() if v}
            selected = [f for f in fields if f["id"] in wanted or f["name"] in wanted]
        columns = [f["db_field_name"] for f in fields]
        snapshots: list[dict[str, Any]] = []
        for record_id in ids:
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
            if row is None:
                continue
            snapshots.append(
                {
                    "id": record_id,
                    "v": row["__version"],
                    "type": "json0",
                    "data": self._full_vo(row, selected, "id"),
                }
            )
        return snapshots

    async def socket_doc_ids(
        self, table_id: str, query: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return ``{ids}`` (view/filter/sort ordered) for record subscriptions."""
        query = query or {}
        listing = await self.list_records(
            table_id,
            field_key_type="id",
            view_id=query.get("viewId"),
            filter_param=query.get("filter"),
            order_by=query.get("orderBy"),
            take=query.get("take") or DEFAULT_TAKE,
            skip=query.get("skip") or 0,
        )
        return {"ids": [r["id"] for r in listing["records"]]}

    async def _compute_insert_order(
        self, table: dict[str, Any], order: RecordInsertOrder, count: int
    ) -> tuple[str, list[float]]:
        # appendRecordOrderIndexes -> updateRecordOrdersInner -> updateMultipleOrders:
        # lazily materialize the view row-order column, then space `count` new
        # rows beyond the anchor toward its next neighbour.
        base_id = table["base_id"]
        table_id = table["id"]
        column = f"__row_{order.viewId}"
        await repository.ensure_view_order_column(base_id, table_id, column)
        anchor_value = await repository.get_column_value(
            base_id, table_id, column, order.anchorId
        )
        if anchor_value is None:
            raise _anchor_not_found(order.anchorId)
        below = order.position == "after"
        for _ in range(64):
            neighbor = await repository.neighbor_order_value(
                base_id, table_id, column, anchor_value, below, []
            )
            next_order = (
                neighbor if neighbor is not None else anchor_value + (1 if below else -1)
            )
            gap = abs((anchor_value - next_order) / (count + 1))
            if gap < _ORDER_EPSILON:
                await repository.shuffle_view_order(base_id, table_id, column)
                anchor_value = await repository.get_column_value(
                    base_id, table_id, column, order.anchorId
                )
                continue
            order_base = anchor_value if below else next_order
            return column, [order_base + gap * (i + 1) for i in range(count)]
        return column, [anchor_value + (i + 1) for i in range(count)]

    async def create_records(self, table_id: str, body: RecordCreateBody) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        user_id = cls.get("user.id")
        self_primary_col = next(
            (f["db_field_name"] for f in fields if f.get("is_primary")), None
        )
        link_cache: dict[str, Any] = {}
        coll_cache: dict[str, Any] = {}
        columns = [f["db_field_name"] for f in fields]
        records_vo = []
        created_ops: list[dict[str, Any]] = []
        computed_rows: list[dict[str, Any]] = []
        order_column: str | None = None
        order_values: list[float] = []
        if body.order is not None:
            order_column, order_values = await self._compute_insert_order(
                table, body.order, len(body.records)
            )
        for idx, item in enumerate(body.records):
            record_id = new_id(IdPrefix.RECORD)
            values: dict[str, Any] = {
                "__id": record_id,
                "__created_by": user_id,
                **self._create_audit_stamp(user_id),
                "__version": 1,
            }
            if order_column is not None:
                values[order_column] = order_values[idx]
            id_fields: dict[str, Any] = {}
            link_writes: list[tuple[LinkFieldContext, list[str]]] = []
            item_fields = self._with_default_values(
                fields, item.fields, body.fieldKeyType, user_id
            )
            for key, value in item_fields.items():
                field = self._field_by_key(fields, key, body.fieldKeyType)
                if not body.typecast:
                    _validate_cell_value(field, value)
                elif field["type"] in _VALIDATED_CELL_TYPES:
                    value = await self._typecast_cell_value(table_id, field, value)
                if field["type"] == "user" and not body.typecast and value is not None:
                    await self._check_user_cell(table["base_id"], value, coll_cache)
                if field["type"] == "link":
                    ctx = await self._link_ctx(field, link_cache)
                    cell = await resolve_link_cell(ctx, value) if ctx else None
                    values[field["db_field_name"]] = (
                        json.dumps(cell, separators=(",", ":")) if cell is not None else None
                    )
                    if ctx:
                        await self._ensure_foreign_records_exist(
                            ctx, link_input_ids(value), "insert"
                        )
                        values.update(own_row_fk_values(ctx, link_input_ids(value)))
                        link_writes.append((ctx, link_input_ids(value)))
                    id_fields[field["id"]] = value
                    continue
                if field["type"] == "attachment" and value is not None:
                    value = await self._resolve_attachment_cell(value)
                values[field["db_field_name"]] = self._to_db_value(field, value)
                id_fields[field["id"]] = value
            await self._materialize_formulas(fields, record_id, values)
            try:
                await repository.insert_row(table["base_id"], table_id, values)
            except IntegrityError as exc:
                err = _unique_violation_error(exc, fields, "insert")
                if err is not None:
                    raise err from exc
                raise
            for ctx, new_ids in link_writes:
                await self._sync_link_write(table, ctx, record_id, [], new_ids, self_primary_col)
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
            records_vo.append(self._full_vo(row, fields, body.fieldKeyType))
            computed_rows.append(row)
            created_ops.append({"id": record_id, "fields": id_fields})
            from ...realtime.broadcast import broadcast_record_create

            await broadcast_record_create(
                table_id, record_id, {"id": record_id, "fields": {}}
            )
        # the create echo carries computed (lookup/rollup) cells alongside the
        # written fields, matching the reference's post-calc create response.
        await self._apply_computed_fields(
            fields, computed_rows, records_vo, fields, body.fieldKeyType
        )
        created_ids = [op["id"] for op in created_ops]
        await self._materialize_computed(table, fields, created_ids)
        await self._recompute_dependents(table_id, created_ids)
        await self._push_create_op(table_id, created_ops)
        if created_ops:
            from ...realtime.broadcast import broadcast_action_trigger

            await broadcast_action_trigger(table_id, [{"actionKey": "addRecord"}])
        await self._resolve_user_fields(records_vo, fields, body.fieldKeyType)
        return {"records": [self._mutation_echo(vo) for vo in records_vo]}

    async def update_record(
        self, table_id: str, record_id: str, body: RecordPatchBody
    ) -> dict[str, Any]:
        if not body.record.fields:
            raise ApiError("Internal server error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        _require_record_id(record_id)
        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
        if row is None:
            raise _record_not_found()
        old_row = dict(row)
        self_primary_col = next(
            (f["db_field_name"] for f in fields if f.get("is_primary")), None
        )
        link_cache: dict[str, Any] = {}
        coll_cache: dict[str, Any] = {}
        updates = {}
        changed_fields: list[dict[str, Any]] = []
        link_writes: list[tuple[LinkFieldContext, list[str], list[str]]] = []
        for key, value in body.record.fields.items():
            field = self._field_by_key(fields, key, body.fieldKeyType)
            if not body.typecast:
                _validate_cell_value(field, value)
            elif field["type"] in _VALIDATED_CELL_TYPES:
                value = await self._typecast_cell_value(table_id, field, value)
            if field["type"] == "user" and not body.typecast and value is not None:
                await self._check_user_cell(table["base_id"], value, coll_cache)
            if field["type"] == "link":
                ctx = await self._link_ctx(field, link_cache)
                cell = await resolve_link_cell(ctx, value) if ctx else None
                updates[field["db_field_name"]] = (
                    json.dumps(cell, separators=(",", ":")) if cell is not None else None
                )
                if ctx:
                    await self._ensure_foreign_records_exist(
                        ctx, link_input_ids(value), "update"
                    )
                    updates.update(own_row_fk_values(ctx, link_input_ids(value)))
                    prev_ids = link_input_ids(
                        self._from_db_value(field, old_row.get(field["db_field_name"]))
                    )
                    link_writes.append((ctx, prev_ids, link_input_ids(value)))
                changed_fields.append(field)
                continue
            if field["type"] == "attachment" and value is not None:
                value = await self._resolve_attachment_cell(value)
            updates[field["db_field_name"]] = self._to_db_value(field, value)
            changed_fields.append(field)
        if updates:
            await self._materialize_formulas(
                fields, record_id, {**old_row, **updates}, updates
            )
            try:
                await repository.update_row(table["base_id"], table_id, record_id, updates)
            except IntegrityError as exc:
                err = _unique_violation_error(exc, fields, "update")
                if err is not None:
                    raise err from exc
                raise
            for ctx, prev_ids, new_ids in link_writes:
                await self._sync_link_write(
                    table, ctx, record_id, prev_ids, new_ids, self_primary_col
                )
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns) or row
            await self._write_history(table_id, record_id, fields, old_row, row)
            await self._broadcast_edit(table_id, record_id, changed_fields, old_row, row)
            contexts = self._cell_contexts(record_id, changed_fields, old_row, row)
            await self._push_update_op(table_id, [record_id], changed_fields, contexts)
            await self._materialize_computed(table, fields, [record_id])
            await self._recompute_dependents(table_id, [record_id])
            from ...realtime.broadcast import broadcast_action_trigger

            changed_ids = [f["id"] for f in changed_fields]
            await broadcast_action_trigger(
                table_id,
                [{"actionKey": "setRecord", "payload": {"fieldIds": changed_ids}}],
            )
        vo = self._full_vo(row, fields, body.fieldKeyType)
        await self._apply_computed_fields(fields, [row], [vo], fields, body.fieldKeyType)
        await self._presign_attachment_fields([vo], fields, body.fieldKeyType)
        await self._resolve_user_fields([vo], fields, body.fieldKeyType)
        return self._mutation_echo(vo)

    async def update_records(
        self, table_id: str, record_ids: list[str], body: RecordBulkPatchBody
    ) -> list[dict[str, Any]]:
        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        self_primary_col = next(
            (f["db_field_name"] for f in fields if f.get("is_primary")), None
        )
        link_cache: dict[str, Any] = {}
        coll_cache: dict[str, Any] = {}
        result_rows: list[dict[str, Any]] = []
        by_id = {r.id: r.fields for r in body.records}
        all_contexts: list[dict[str, Any]] = []
        all_changed: dict[str, dict[str, Any]] = {}
        touched_ids: list[str] = []
        for record_id in record_ids:
            fields_ro = by_id.get(record_id)
            if fields_ro is None:
                continue
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
            if row is None:
                raise _record_not_found()
            old_row = dict(row)
            updates = {}
            changed_fields: list[dict[str, Any]] = []
            link_writes: list[tuple[LinkFieldContext, list[str], list[str]]] = []
            for key, value in fields_ro.items():
                field = self._field_by_key(fields, key, body.fieldKeyType)
                if not body.typecast:
                    _validate_cell_value(field, value)
                elif field["type"] in _VALIDATED_CELL_TYPES:
                    value = await self._typecast_cell_value(table_id, field, value)
                if field["type"] == "user" and not body.typecast and value is not None:
                    await self._check_user_cell(table["base_id"], value, coll_cache)
                if field["type"] == "link":
                    ctx = await self._link_ctx(field, link_cache)
                    cell = await resolve_link_cell(ctx, value) if ctx else None
                    updates[field["db_field_name"]] = (
                        json.dumps(cell, separators=(",", ":")) if cell is not None else None
                    )
                    if ctx:
                        await self._ensure_foreign_records_exist(
                            ctx, link_input_ids(value), "update"
                        )
                        updates.update(own_row_fk_values(ctx, link_input_ids(value)))
                        prev_ids = link_input_ids(
                            self._from_db_value(field, old_row.get(field["db_field_name"]))
                        )
                        link_writes.append((ctx, prev_ids, link_input_ids(value)))
                    changed_fields.append(field)
                    continue
                if field["type"] == "attachment" and value is not None:
                    value = await self._resolve_attachment_cell(value)
                updates[field["db_field_name"]] = self._to_db_value(field, value)
                changed_fields.append(field)
            if updates:
                await self._materialize_formulas(
                    fields, record_id, {**old_row, **updates}, updates
                )
                try:
                    await repository.update_row(table["base_id"], table_id, record_id, updates)
                except IntegrityError as exc:
                    err = _unique_violation_error(exc, fields, "update")
                    if err is not None:
                        raise err from exc
                    raise
                for ctx, prev_ids, new_ids in link_writes:
                    await self._sync_link_write(
                        table, ctx, record_id, prev_ids, new_ids, self_primary_col
                    )
                row = (
                    await repository.fetch_row(table["base_id"], table_id, record_id, columns)
                    or row
                )
                await self._write_history(table_id, record_id, fields, old_row, row)
                await self._broadcast_edit(table_id, record_id, changed_fields, old_row, row)
                all_contexts.extend(
                    self._cell_contexts(record_id, changed_fields, old_row, row)
                )
                for field in changed_fields:
                    all_changed[field["id"]] = field
                touched_ids.append(record_id)
                await self._materialize_computed(table, fields, [record_id])
                row = (
                    await repository.fetch_row(table["base_id"], table_id, record_id, columns)
                    or row
                )
            result_rows.append(row)
        if touched_ids:
            await self._recompute_dependents(table_id, touched_ids)
            from ...realtime.broadcast import broadcast_action_trigger

            await broadcast_action_trigger(
                table_id,
                [{"actionKey": "setRecord", "payload": {"fieldIds": list(all_changed.keys())}}],
            )
        if all_contexts:
            await self._push_update_op(
                table_id, touched_ids, list(all_changed.values()), all_contexts
            )
        results = [self._full_vo(row, fields, body.fieldKeyType) for row in result_rows]
        await self._apply_computed_fields(fields, result_rows, results, fields, body.fieldKeyType)
        await self._resolve_user_fields(results, fields, body.fieldKeyType)
        return [self._mutation_echo(vo) for vo in results]

    async def delete_record(
        self, table_id: str, record_id: str
    ) -> dict[str, Any] | None:
        _require_record_id(record_id)
        table, fields = await self._load_context(table_id)
        row = await repository.fetch_row(
            table["base_id"], table_id, record_id, [f["db_field_name"] for f in fields]
        )
        if row is None:
            return None
        vo = self._full_vo(row, fields, "id")
        await self._apply_computed_fields(fields, [row], [vo], fields, "id")
        await self._write_record_trash(table_id, fields, [row])
        await repository.delete_row(table["base_id"], table_id, record_id)
        from ...realtime.broadcast import broadcast_record_delete

        await broadcast_record_delete(table_id, record_id, int(row.get("__version") or 1))
        await self._recompute_dependents(table_id, [record_id])
        from ...realtime.broadcast import broadcast_action_trigger

        await broadcast_action_trigger(table_id, [{"actionKey": "deleteRecord"}])
        await self._push_delete_op(table_id, [self._delete_echo(record_id, row, fields)])
        return self._delete_echo_vo(vo, fields)

    async def _write_record_trash(
        self, table_id: str, fields: list[dict[str, Any]], rows: list[dict[str, Any]]
    ) -> None:
        # mirror the reference table_trash/record_trash snapshots: one table_trash
        # row per delete operation (snapshot = the deleted record id list) plus a
        # per-record record_trash snapshot of the full record. wedoc writes both
        # synchronously (the reference projects record_trash asynchronously).
        if not rows:
            return
        from ..trash import repository as trash_repository

        user_id = cls.get("user.id")
        record_ids = [row["__id"] for row in rows]
        await trash_repository.insert_table_trash(
            new_id(IdPrefix.OPERATION),
            table_id,
            "record",
            json.dumps(record_ids, ensure_ascii=False),
            user_id,
        )
        trash_rows = []
        for row in rows:
            vo = self._full_vo(row, fields, "id")
            snapshot = {
                "id": vo["id"],
                "name": vo["name"],
                "fields": vo["fields"],
                "version": int(row.get("__version") or 1),
                "createdBy": vo["createdBy"],
                "autoNumber": vo["autoNumber"],
                "createdTime": vo["createdTime"],
                "lastModifiedBy": vo.get("lastModifiedBy"),
                "lastModifiedTime": vo.get("lastModifiedTime"),
            }
            trash_rows.append(
                {
                    "id": new_id(IdPrefix.RECORD_TRASH),
                    "table_id": table_id,
                    "record_id": row["__id"],
                    "snapshot": json.dumps(snapshot, ensure_ascii=False),
                    "created_by": user_id,
                }
            )
        await trash_repository.insert_record_trash(trash_rows)

    async def delete_records(
        self, table_id: str, record_ids: list[str]
    ) -> dict[str, Any]:
        from ...realtime.broadcast import broadcast_record_delete

        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        rows_by_id: dict[str, dict[str, Any]] = {}
        for rid in record_ids:
            if rid in rows_by_id:
                continue
            row = await repository.fetch_row(table["base_id"], table_id, rid, columns)
            if row is not None:
                rows_by_id[rid] = row
        missing = [rid for rid in record_ids if rid not in rows_by_id]
        if missing:
            raise ApiError(
                f"Some records to be deleted cannot be found, ids: {','.join(missing)}",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.record.deletedIdsNotFound"}},
            )
        echoes = []
        deleted_rows = [rows_by_id[rid] for rid in record_ids]
        vos = [self._full_vo(row, fields, "id") for row in deleted_rows]
        await self._apply_computed_fields(fields, deleted_rows, vos, fields, "id")
        for rid in record_ids:
            row = rows_by_id[rid]
            await repository.delete_row(table["base_id"], table_id, rid)
            await broadcast_record_delete(table_id, rid, int(row.get("__version") or 1))
            echoes.append(self._delete_echo(rid, row, fields))
        await self._write_record_trash(table_id, fields, deleted_rows)
        await self._recompute_dependents(table_id, [r["__id"] for r in deleted_rows])
        await self._push_delete_op(table_id, echoes)
        if deleted_rows:
            from ...realtime.broadcast import broadcast_action_trigger

            await broadcast_action_trigger(table_id, [{"actionKey": "deleteRecord"}])
        return {"records": [self._delete_echo_vo(vo, fields) for vo in vos]}

    async def duplicate_record(
        self, table_id: str, record_id: str, order: RecordInsertOrder | None = None
    ) -> dict[str, Any]:
        _require_record_id(record_id)
        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
        if row is None:
            # ref duplicate path uses the localized not-found (i18nKey), not the
            # v2 domain-error shape used by update.
            raise _record_not_found(localization=True)
        new_record_id = new_id(IdPrefix.RECORD)
        user_id = cls.get("user.id")
        self_primary_col = next(
            (f["db_field_name"] for f in fields if f.get("is_primary")), None
        )
        values: dict[str, Any] = {
            "__id": new_record_id,
            "__created_by": user_id,
            **self._create_audit_stamp(user_id),
            "__version": 1,
        }
        if order is not None:
            order_column, order_values = await self._compute_insert_order(table, order, 1)
            values[order_column] = order_values[0]
        # rebuild each cell through its write path so structured cells are stored
        # in their serialized form (a raw copy of the fetched dict/list is not a
        # valid bind) and links re-establish their FK / junction rows on the copy.
        link_cache: dict[str, Any] = {}
        link_writes: list[tuple[LinkFieldContext, list[str]]] = []
        for field in fields:
            if _is_computed_field(field):
                continue
            column = field["db_field_name"]
            value = self._from_db_value(field, row.get(column))
            if field["type"] == "link":
                ctx = await self._link_ctx(field, link_cache)
                if ctx is None:
                    continue
                ids = link_input_ids(value)
                await self._ensure_foreign_records_exist(ctx, ids, "insert")
                cell = await resolve_link_cell(ctx, value)
                values[column] = (
                    json.dumps(cell, separators=(",", ":")) if cell is not None else None
                )
                values.update(own_row_fk_values(ctx, ids))
                if ids:
                    link_writes.append((ctx, ids))
                continue
            values[column] = self._to_db_value(field, value)
        await self._materialize_formulas(fields, new_record_id, values)
        try:
            await repository.insert_row(table["base_id"], table_id, values)
        except IntegrityError as exc:
            err = _unique_violation_error(exc, fields, "insert")
            if err is not None:
                raise err from exc
            raise
        for ctx, new_ids in link_writes:
            await self._sync_link_write(table, ctx, new_record_id, [], new_ids, self_primary_col)
        new_row = await repository.fetch_row(table["base_id"], table_id, new_record_id, columns)
        vo = self._full_vo(new_row, fields, "id")
        await self._apply_computed_fields(fields, [new_row], [vo], fields, "id")
        await self._materialize_computed(table, fields, [new_record_id])
        await self._recompute_dependents(table_id, [new_record_id])
        await self._presign_attachment_fields([vo], fields, "id")
        await self._resolve_user_fields([vo], fields, "id")
        return self._mutation_echo(vo)

    # ---- record history ------------------------------------------------------

    async def _write_history(
        self,
        table_id: str,
        record_id: str,
        fields: list[dict[str, Any]],
        old_row: dict[str, Any],
        new_row: dict[str, Any],
    ) -> None:
        # one row per changed field; equal values (incl. no-op patches) skip.
        user_id = cls.get("user.id")
        rows = []
        for field in fields:
            if field.get("is_computed"):
                continue
            column = field["db_field_name"]
            entry = build_history_row(
                table_id=table_id,
                record_id=record_id,
                field_id=field["id"],
                name=field["name"],
                field_type=field["type"],
                options_raw=field.get("options"),
                cell_value_type=field["cell_value_type"],
                before=self._from_db_value(field, old_row.get(column)),
                after=self._from_db_value(field, new_row.get(column)),
                user_id=user_id,
            )
            if entry:
                rows.append(entry)
        if rows:
            # ref writes one batch per update: all entries share a single
            # created_time and surface in field order (newest update first, fields
            # ascending). Assign sorted ids to the field-ordered rows so the
            # (created_time DESC, id ASC) read returns that order deterministically.
            created_time = datetime.now(UTC).replace(tzinfo=None)
            for row, hid in zip(rows, sorted(r["id"] for r in rows), strict=True):
                row["created_time"] = created_time
                row["id"] = hid
            await repository.insert_history(rows)

    async def get_history(
        self,
        table_id: str,
        record_id: str | None,
        cursor: str | None,
        start_date: str | None,
        end_date: str | None,
        field_ids: list[str] | None,
        created_by_ids: list[str] | None,
    ) -> dict[str, Any]:
        await self._load_context(table_id)
        cursor_time = cursor_id = None
        if cursor and cursor.startswith("chs1:"):
            try:
                decoded = json.loads(base64.b64decode(cursor[5:]))
                cursor_time, cursor_id = decoded["t"], decoded["id"]
            except (ValueError, KeyError, json.JSONDecodeError):
                cursor_time = cursor_id = None
        rows = await repository.list_history(
            table_id,
            record_id=record_id,
            start_date=start_date,
            end_date=end_date,
            field_ids=field_ids,
            created_by_ids=created_by_ids,
            cursor_time=cursor_time,
            cursor_id=cursor_id,
            limit=21,
        )
        result: dict[str, Any] = {}
        if len(rows) > 20:
            rows = rows[:20]
            last = rows[-1]
            result["nextCursor"] = "chs1:" + base64.b64encode(
                json.dumps({"t": _iso(last["created_time"]), "id": last["id"]}).encode()
            ).decode()
        created_by = list({r["created_by"] for r in rows})
        users = await repository.get_users_by_ids(created_by) if created_by else []
        user_map = {
            u["id"]: {
                "id": u["id"],
                "name": u["name"],
                "email": u["email"],
                "avatar": get_public_full_storage_url(u["avatar"]) if u["avatar"] else None,
            }
            for u in users
        }
        result["historyList"] = [
            {
                "id": r["id"],
                "tableId": r["table_id"],
                "recordId": r["record_id"],
                "fieldId": r["field_id"],
                "before": json.loads(r["before"]),
                "after": json.loads(r["after"]),
                "createdTime": _iso(r["created_time"]),
                "createdBy": r["created_by"],
            }
            for r in rows
        ]
        result["userMap"] = user_map
        return result

    # ---- form submit ---------------------------------------------------------

    async def form_submit(self, table_id: str, body: RecordSubmitBody) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        view = await get_view_row(table_id, body.viewId)
        if view is None:
            raise ApiError(
                f"View not found: {body.viewId}",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "view.not_found", "domainTags": ["not-found"]},
            )
        if view["type"] != "form":
            raise ApiError(
                "View is not a form",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"domainCode": "view.type_not_form", "domainTags": ["forbidden"]},
            )
        column_meta = json.loads(view.get("column_meta") or "{}")
        field_id_set = {f["id"] for f in fields}
        visible_ids = {
            fid for fid, meta in column_meta.items() if meta.get("visible") is True
        } & field_id_set
        if (not visible_ids and body.fields) or any(
            key not in visible_ids for key in body.fields
        ):
            raise ApiError(
                "The form contains hidden fields, submission not allowed.",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {
                    "domainCode": "view.hidden_fields_submission_not_allowed",
                    "domainTags": ["forbidden"],
                },
            )
        record_id = new_id(IdPrefix.RECORD)
        user_id = cls.get("user.id")
        self_primary_col = next(
            (f["db_field_name"] for f in fields if f.get("is_primary")), None
        )
        values: dict[str, Any] = {
            "__id": record_id,
            "__created_by": user_id,
            **self._create_audit_stamp(user_id),
            "__version": 1,
        }
        link_cache: dict[str, Any] = {}
        coll_cache: dict[str, Any] = {}
        link_writes: list[tuple[LinkFieldContext, list[str]]] = []
        for key, value in body.fields.items():
            field = self._field_by_key(fields, key, "id")
            if field["type"] == "user" and not body.typecast and value is not None:
                await self._check_user_cell(table["base_id"], value, coll_cache)
            if field["type"] == "link":
                ctx = await self._link_ctx(field, link_cache)
                cell = await resolve_link_cell(ctx, value) if ctx else None
                values[field["db_field_name"]] = (
                    json.dumps(cell, separators=(",", ":")) if cell is not None else None
                )
                if ctx:
                    ids = link_input_ids(value)
                    await self._ensure_foreign_records_exist(ctx, ids, "insert")
                    values.update(own_row_fk_values(ctx, ids))
                    if ids:
                        link_writes.append((ctx, ids))
                continue
            if field["type"] == "attachment" and value is not None:
                value = await self._resolve_attachment_cell(value)
            values[field["db_field_name"]] = self._to_db_value(field, value)
        await self._materialize_formulas(fields, record_id, values)
        try:
            await repository.insert_row(table["base_id"], table_id, values)
        except IntegrityError as exc:
            err = _unique_violation_error(exc, fields, "insert")
            if err is not None:
                raise err from exc
            raise
        for ctx, new_ids in link_writes:
            await self._sync_link_write(table, ctx, record_id, [], new_ids, self_primary_col)
        await self._materialize_computed(table, fields, [record_id])
        await self._recompute_dependents(table_id, [record_id])
        columns = [f["db_field_name"] for f in fields]
        row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
        vo = self._full_vo(row, fields, "id")
        await self._apply_computed_fields(fields, [row], [vo], fields, "id")
        await self._presign_attachment_fields([vo], fields, "id")
        await self._resolve_user_fields([vo], fields, "id")
        return self._mutation_echo(vo)

    async def get_status(
        self,
        table_id: str,
        record_id: str,
        *,
        view_id: str | None = None,
        filter_param: dict[str, Any] | None = None,
        order_by: list[dict[str, Any]] | None = None,
        group_by: list[dict[str, Any]] | None = None,
        collapsed_group_ids: list[str] | None = None,
        search: list[Any] | None = None,
        ignore_view_query: bool = False,
        take: int = DEFAULT_TAKE,
        skip: int = 0,
    ) -> dict[str, Any]:
        _require_record_id(record_id)
        table, _fields = await self._load_context(table_id)
        row = await repository.fetch_row(table["base_id"], table_id, record_id, [])
        if row is None:
            return {"isDeleted": True, "isVisible": False}
        listing = await self.list_records(
            table_id,
            field_key_type="id",
            view_id=view_id,
            filter_param=filter_param,
            order_by=order_by,
            group_by=group_by,
            collapsed_group_ids=collapsed_group_ids,
            search=search,
            ignore_view_query=ignore_view_query,
            take=take,
            skip=skip,
        )
        is_visible = any(r["id"] == record_id for r in listing["records"])
        return {"isDeleted": False, "isVisible": is_visible}

    # -- collaborators ---------------------------------------------------------
    async def get_records_collaborators(
        self, table_id: str, query: dict[str, Any]
    ) -> list[dict[str, Any]]:
        field_id = query["fieldId"]
        table, fields = await self._load_context(table_id)
        field = next((f for f in fields if f["id"] == field_id), None)
        if field is None or field["type"] not in ("user", "createdBy", "lastModifiedBy"):
            raise ApiError(
                "field type is not user-related field",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {
                    "domainCode": "record_collaborators.field_not_user_related",
                    "domainTags": ["forbidden"],
                    "details": {"fieldId": field_id},
                },
            )
        base_id = table["base_id"]
        if field["type"] == "createdBy":
            raw = await repository.distinct_column_values(base_id, table_id, "__created_by")
            user_ids = [v for v in raw if v]
        elif field["type"] == "lastModifiedBy":
            raw = await repository.distinct_column_values(
                base_id, table_id, "__last_modified_by"
            )
            user_ids = [v for v in raw if v]
        else:  # user field — cell stores {id,...} or [{id,...}]
            raw = await repository.distinct_column_values(
                base_id, table_id, field["db_field_name"]
            )
            user_ids = []
            for cell in raw:
                parsed = cell if isinstance(cell, (dict, list)) else json.loads(cell)
                for item in parsed if isinstance(parsed, list) else [parsed]:
                    if isinstance(item, dict) and item.get("id"):
                        user_ids.append(item["id"])
        user_ids = list(dict.fromkeys(user_ids))
        if not user_ids:
            return []
        users = await repository.get_users_by_ids(user_ids)
        search = query.get("search")
        if search:
            low = search.lower()
            users = [
                u
                for u in users
                if low in (u["name"] or "").lower() or low in (u["email"] or "").lower()
            ]
        skip = query.get("skip") or 0
        take = query.get("take") or 50
        users = users[skip : skip + take]
        return [
            {
                "userId": u["id"],
                "userName": u["name"],
                "email": u["email"],
                "avatar": get_public_full_storage_url(u["avatar"]) if u["avatar"] else None,
            }
            for u in users
        ]

    # -- button field ----------------------------------------------------------
    async def _load_button_field(
        self, table_id: str, field_id: str
    ) -> dict[str, Any]:
        _table, fields = await self._load_context(table_id)
        field = next((f for f in fields if f["id"] == field_id), None)
        if field is None or field["type"] != "button":
            raise ApiError(
                "Field is not a Button field",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "domainCode": "button.field_type_invalid",
                    "domainTags": ["validation"],
                    "details": {"fieldId": field_id},
                },
            )
        return field

    async def button_click(
        self, table_id: str, record_id: str, field_id: str
    ) -> dict[str, Any]:
        field = await self._load_button_field(table_id, field_id)
        options = _field_options(field)
        workflow = options.get("workflow") or {}
        is_active = bool(workflow.get("id") and workflow.get("isActive"))
        if not is_active:
            raise ApiError(
                f"Button field's workflow {workflow.get('id', 'undefined')} is not active",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.workflow.notActive"}},
            )
        record = await self.get_record(
            table_id, record_id, field_key_type="id", projection=[field_id]
        )
        cell = record["fields"].get(field_id)
        count = int(cell["count"]) if isinstance(cell, dict) and cell.get("count") else 0
        max_count = options.get("maxCount")
        if max_count is not None and count >= max_count:
            raise ApiError(
                f"Button click count {count} reached max count {max_count}",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.field.button.clickCountReachedMaxCount"
                    }
                },
            )
        patch = RecordPatchBody.zod_validate(
            {
                "fieldKeyType": "id",
                "record": {"fields": {field_id: {"count": count + 1}}},
            }
        )
        updated = await self.update_record(table_id, record_id, patch)
        updated["fields"] = {field_id: updated["fields"].get(field_id)}
        return {"tableId": table_id, "fieldId": field_id, "record": updated}

    async def reset_button(
        self, table_id: str, record_id: str, field_id: str
    ) -> dict[str, Any]:
        field = await self._load_button_field(table_id, field_id)
        options = _field_options(field)
        if not options.get("resetCount"):
            raise ApiError(
                "Button field does not support reset",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "domainCode": "button.reset_not_supported",
                    "domainTags": ["validation"],
                    "details": {
                        "fieldId": field_id,
                        "i18nKey": "httpErrors.field.button.notSupportReset",
                    },
                },
            )
        patch = RecordPatchBody.zod_validate(
            {"fieldKeyType": "id", "record": {"fields": {field_id: None}}}
        )
        return await self.update_record(table_id, record_id, patch)

    # -- attachment cell endpoints --------------------------------------------
    async def _validate_attachment_record(
        self, table_id: str, record_id: str, field_id: str
    ) -> dict[str, Any]:
        _table, fields = await self._load_context(table_id)
        field = next((f for f in fields if f["id"] == field_id), None)
        if field is None:
            # matches ref's pre-service field-scope validation shape.
            raise ApiError(
                "Field not found",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "not_found", "domainTags": ["not-found"]},
            )
        if field["type"] != "attachment":
            raise ApiError(
                "Field is not an attachment",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.field.notAttachment"}},
            )
        if field.get("is_computed"):
            raise ApiError(
                "Field is computed",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.field.isComputed"}},
            )
        return await self.get_record(table_id, record_id, "id")

    async def _upload_attachment_file(
        self, data: bytes, filename: str, content_type: str
    ) -> dict[str, Any]:
        import mimetypes

        from ..attachment import schemas as att_schemas
        from ..attachment.service import AttachmentService

        resolved_type = content_type
        if content_type == "application/octet-stream":
            guessed = mimetypes.guess_type(filename)[0]
            if guessed:
                resolved_type = guessed
        service = AttachmentService()
        sig = await service.signature(
            att_schemas.SignatureRo(
                contentType=resolved_type,
                contentLength=len(data),
                type=att_schemas.UploadType.TABLE,
            )
        )
        await service.upload(data, resolved_type, sig["token"])
        notify = await service.notify(sig["token"], filename)
        return {**notify, "id": new_id(IdPrefix.ATTACHMENT), "name": filename}

    async def upload_attachment(
        self,
        table_id: str,
        record_id: str,
        field_id: str,
        file: dict[str, Any] | None,
        file_url: str | None,
    ) -> dict[str, Any]:
        if not file and not file_url:
            raise ApiError(
                "No file or URL provided",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.record.noFileOrUrlProvided"}},
            )
        record = await self._validate_attachment_record(table_id, record_id, field_id)
        if file is not None:
            item = await self._upload_attachment_file(
                file["bytes"], file["filename"], file["content_type"]
            )
        else:
            item = await self._upload_attachment_from_url(file_url or "")
        current = record["fields"].get(field_id) or []
        body = RecordPatchBody.model_construct(
            record=RecordItem.model_construct(fields={field_id: [*current, item]}),
            fieldKeyType="id",
        )
        return await self.update_record(table_id, record_id, body)

    async def _upload_attachment_from_url(self, file_url: str) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            data = resp.content
            content_type = resp.headers.get("content-type", "application/octet-stream")
        filename = file_url.rstrip("/").split("/")[-1] or "file"
        return await self._upload_attachment_file(data, filename, content_type)

    async def insert_attachment(
        self,
        table_id: str,
        record_id: str,
        field_id: str,
        attachments: list[dict[str, Any]],
        anchor_id: str | None,
    ) -> dict[str, Any]:
        if not attachments:
            raise ApiError("No attachments provided", HttpErrorCode.VALIDATION_ERROR)
        record = await self._validate_attachment_record(table_id, record_id, field_id)
        current = record["fields"].get(field_id) or []
        anchor_index = -1
        if anchor_id:
            anchor_index = next(
                (i for i, item in enumerate(current) if item.get("id") == anchor_id), -1
            )
        if anchor_index >= 0:
            nxt = [
                *current[: anchor_index + 1],
                *attachments,
                *current[anchor_index + 1 :],
            ]
        else:
            nxt = [*current, *attachments]
        body = RecordPatchBody.model_construct(
            record=RecordItem.model_construct(fields={field_id: nxt}),
            fieldKeyType="id",
        )
        return await self.update_record(table_id, record_id, body)
