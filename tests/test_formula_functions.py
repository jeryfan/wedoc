"""Formula function coverage: the common subset (MID/SEARCH/REPT) plus the
date/time, numeric, text, count, boolean and system functions ported to match
the live oracle.

MID uses 1-based start with JS substring semantics; SEARCH returns the 1-based
position (case-sensitive) or blank; REPT repeats the string. The oracle-matched
functions assert values confirmed by live A/B (e.g. natural-log default LOG,
verbatim REGEXP_REPLACE, first-delimiter TEXTBEFORE, boolean false surfaced as
false rather than blank).
"""

import math

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _formula_cells(client, name_value, exprs):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    fids = {}
    for label, expr in exprs.items():
        e = expr.replace("N", "{" + name_id + "}")
        fid = (
            await _create(
                client,
                f"/api/table/{tid}/field",
                {"name": label, "type": "formula", "options": {"expression": e}},
            )
        )["id"]
        fids[label] = fid
    await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {name_id: name_value}}]},
    )
    listed = await client.get(
        f"/api/table/{tid}/record", params={"fieldKeyType": "id", "take": "1"}
    )
    rec = listed.json()["records"][0]["fields"]
    return {label: rec.get(fid) for label, fid in fids.items()}


async def test_mid_is_one_based_with_substring_semantics(client):
    cells = await _formula_cells(
        client,
        "hello",
        {
            "m0": "MID(N,0,2)",
            "m1": "MID(N,1,2)",
            "m2": "MID(N,2,3)",
            "m5": "MID(N,5,3)",
            "mbig": "MID(N,10,3)",
        },
    )
    assert cells == {"m0": "h", "m1": "he", "m2": "ell", "m5": "o", "mbig": ""}


async def test_search_and_rept(client):
    cells = await _formula_cells(
        client,
        "hello",
        {"search": 'SEARCH("l", N)', "nofind": 'SEARCH("z", N)', "rept": 'REPT("ab", 3)'},
    )
    assert cells["search"] == 3
    assert cells["nofind"] in (None, "")
    assert cells["rept"] == "ababab"


_DATE = "2024-06-15T00:00:00.000Z"  # a Saturday
_DATE2 = "2024-01-10T00:00:00.000Z"


async def _compute(client, exprs):
    """Set up Name(text)/Num/D/D2(date, UTC) + formula fields, one record; read cells.

    Expressions use multi-char placeholders (@NAME@/@NUM@/@DATE@/@DATE2@) so that
    substituting a field id never corrupts a function name.
    """
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    num = await _create(client, f"/api/table/{tid}/field", {"name": "Num", "type": "number"})
    fmt = {"formatting": {"date": "YYYY-MM-DD", "time": "None", "timeZone": "UTC"}}
    d = await _create(
        client, f"/api/table/{tid}/field", {"name": "D", "type": "date", "options": fmt}
    )
    d2 = await _create(
        client, f"/api/table/{tid}/field", {"name": "D2", "type": "date", "options": fmt}
    )
    num_id, d_id, d2_id = num["id"], d["id"], d2["id"]

    def _sub(expr):
        return (
            expr.replace("@NAME@", "{" + name_id + "}")
            .replace("@NUM@", "{" + num_id + "}")
            .replace("@DATE2@", "{" + d2_id + "}")
            .replace("@DATE@", "{" + d_id + "}")
        )

    fids = {}
    for label, expr in exprs.items():
        created = await _create(
            client,
            f"/api/table/{tid}/field",
            {"name": label, "type": "formula", "options": {"expression": _sub(expr)}},
        )
        fids[label] = created["id"]
    await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [
            {"fields": {name_id: "hello world", num_id: 3, d_id: _DATE, d2_id: _DATE2}}
        ]},
    )
    listed = await client.get(
        f"/api/table/{tid}/record", params={"fieldKeyType": "id", "take": "1"}
    )
    rec = listed.json()["records"][0]["fields"]
    return {label: rec.get(fid) for label, fid in fids.items()}


async def test_numeric_functions_match_oracle(client):
    cells = await _compute(client, {
        "even": "EVEN(2.5)",
        "even_neg": "EVEN(-1)",
        "odd": "ODD(2)",
        "exp": "EXP(0)",
        "log_nat": "LOG(@NUM@)",
        "log_base": "LOG(8, 2)",
    })
    assert cells["even"] == 2
    assert cells["even_neg"] == 0
    assert cells["odd"] == 3
    assert cells["exp"] == 1
    assert cells["log_base"] == 3
    # single-argument LOG is the natural log (oracle default base is e, not 10)
    assert abs(cells["log_nat"] - math.log(3)) < 1e-9


async def test_text_functions_match_oracle(client):
    cells = await _compute(client, {
        "find": 'FIND("l", @NAME@)',
        "regexp": 'REGEXP_REPLACE(@NAME@, "o", "0")',
        "regexp_literal": 'REGEXP_REPLACE("ab", "(a)(b)", "$2$1")',
        "before": 'TEXTBEFORE(@NAME@, " ")',
        "encode": "ENCODE_URL_COMPONENT(@NAME@)",
        "split": 'TEXTSPLIT("a,b,c", ",")',
    })
    assert cells["find"] == 3
    assert cells["regexp"] == "hell0 w0rld"
    # the replacement is inserted verbatim; $1/$2 are not expanded
    assert cells["regexp_literal"] == "$2$1"
    assert cells["before"] == "hello"
    assert cells["encode"] == "hello%20world"
    assert cells["split"] == ["a", "b", "c"]


async def test_date_functions_match_oracle(client):
    cells = await _compute(client, {
        "year": "YEAR(@DATE@)",
        "month": "MONTH(@DATE@)",
        "day": "DAY(@DATE@)",
        "weekday": "WEEKDAY(@DATE@)",
        "weeknum": "WEEKNUM(@DATE@)",
        "datestr": "DATESTR(@DATE@)",
        "add": 'DATE_ADD(@DATE@, 1, "month")',
        "diff": 'DATETIME_DIFF(@DATE@, @DATE2@, "day")',
    })
    assert cells["year"] == 2024
    assert cells["month"] == 6
    assert cells["day"] == 15
    assert cells["weekday"] == 6
    assert cells["weeknum"] == 24
    assert cells["datestr"] == "2024-06-15"
    assert cells["add"] == "2024-07-15T00:00:00.000Z"
    assert cells["diff"] == 157


async def test_count_and_boolean_functions_match_oracle(client):
    cells = await _compute(client, {
        "count": "COUNT(@NUM@)",
        "counta": "COUNTA(@NAME@)",
        "countall": "COUNTALL(@NAME@)",
        "is_after": "IS_AFTER(@DATE@, @DATE2@)",
        "is_before": "IS_BEFORE(@DATE@, @DATE2@)",
    })
    assert cells["count"] == 1
    assert cells["counta"] == 1
    assert cells["countall"] == 1
    assert cells["is_after"] is True
    # a boolean formula evaluating to false surfaces as false, not blank
    assert cells["is_before"] is False


async def test_record_id_and_auto_number(client):
    cells = await _compute(client, {"rid": "RECORD_ID()", "auto": "AUTO_NUMBER()"})
    assert isinstance(cells["rid"], str) and cells["rid"].startswith("rec")
    assert cells["auto"] == 1


async def test_date_add_units_match_oracle(client):
    cells = await _compute(client, {
        "plural": 'DATE_ADD(@DATE@, 1, "months")',
        "days": 'DATE_ADD(@DATE@, 1, "days")',
        "quarter": 'DATE_ADD(@DATE@, 1, "quarter")',
        "mixed_case": 'DATE_ADD(@DATE@, 1, "Month")',
        "abbrev": 'DATE_ADD(@DATE@, 90, "min")',
        "single_char": 'DATE_ADD(@DATE@, 1, "M")',
        "unknown": 'DATE_ADD(@DATE@, 1, "foo")',
    })
    # units are trimmed + case-insensitive; plurals and quarter (x3 months) resolve
    assert cells["plural"] == "2024-07-15T00:00:00.000Z"
    assert cells["days"] == "2024-06-16T00:00:00.000Z"
    assert cells["quarter"] == "2024-09-15T00:00:00.000Z"
    assert cells["mixed_case"] == "2024-07-15T00:00:00.000Z"
    assert cells["abbrev"] == "2024-06-15T01:30:00.000Z"
    # dayjs single-char codes and unknown units are not accepted; the cell is blank
    assert cells["single_char"] in (None, "")
    assert cells["unknown"] in (None, "")


async def test_datetime_diff_units_match_oracle(client):
    # @DATE@ = 2024-06-15, @DATE2@ = 2024-01-10 (157 whole days apart, UTC midnight)
    cells = await _compute(client, {
        "day": 'DATETIME_DIFF(@DATE@, @DATE2@, "day")',
        "months": 'DATETIME_DIFF(@DATE@, @DATE2@, "months")',
        "quarter": 'DATETIME_DIFF(@DATE@, @DATE2@, "quarter")',
        "year": 'DATETIME_DIFF(@DATE@, @DATE2@, "year")',
        "minute": 'DATETIME_DIFF(@DATE@, @DATE2@, "minute")',
        "m_upper": 'DATETIME_DIFF(@DATE@, @DATE2@, "M")',
        "d_single": 'DATETIME_DIFF(@DATE@, @DATE2@, "d")',
        "unknown": 'DATETIME_DIFF(@DATE@, @DATE2@, "foo")',
    })
    assert cells["day"] == 157
    assert cells["months"] == 5
    # quarter is an integer month diff over 3.0 (float); year truncates months / 12
    assert cells["quarter"] == 1.6666666666666667
    assert cells["year"] == 0
    # bare "m"/"M" resolves to minute (157 days -> 226080 minutes), not month
    assert cells["minute"] == 226080
    assert cells["m_upper"] == 226080
    # single-char "d" and unknown units are not accepted; the cell is blank
    assert cells["d_single"] in (None, "")
    assert cells["unknown"] in (None, "")


async def test_date_compare_matches_oracle(client):
    # @DATE@ = 2024-06-15, @DATE2@ = 2024-01-10 (both UTC midnight, same year)
    cells = await _compute(client, {
        "after_raw": "IS_AFTER(@DATE@, @DATE2@)",
        "after_unit_ignored": 'IS_AFTER(@DATE@, @DATE2@, "year")',
        "before_false": "IS_BEFORE(@DATE@, @DATE2@)",
        "before_true": "IS_BEFORE(@DATE2@, @DATE@)",
        "same_year": 'IS_SAME(@DATE@, @DATE2@, "year")',
        "same_month": 'IS_SAME(@DATE@, @DATE2@, "month")',
        "same_quarter": 'IS_SAME(@DATE@, @DATE2@, "quarter")',
        "same_foo": 'IS_SAME(@DATE@, @DATE2@, "foo")',
        "same_noarg": "IS_SAME(@DATE@, @DATE@)",
    })
    # IS_AFTER / IS_BEFORE ignore any unit arg and compare raw instants
    assert cells["after_raw"] is True
    assert cells["after_unit_ignored"] is True
    assert cells["before_false"] is False
    assert cells["before_true"] is True
    # IS_SAME truncates by unit; quarter and unknown units are not accepted (blank)
    assert cells["same_year"] is True
    assert cells["same_month"] is False
    assert cells["same_quarter"] in (None, "")
    assert cells["same_foo"] in (None, "")
    # no-unit IS_SAME is a raw equality check
    assert cells["same_noarg"] is True


async def test_fromnow_tonow_match_oracle(client):
    # now-relative; assert the deterministic contract (sign, unit set, FROMNOW == TONOW).
    # @DATE2@ = 2024-01-10 is in the past relative to any run.
    cells = await _compute(client, {
        "from_year": 'FROMNOW(@DATE2@, "year")',
        "to_year": 'TONOW(@DATE2@, "year")',
        "from_month": 'FROMNOW(@DATE2@, "month")',
        "from_foo": 'FROMNOW(@DATE2@, "foo")',
        "from_d": 'FROMNOW(@DATE2@, "d")',
    })
    # a past date yields a positive, signed now-diff; FROMNOW and TONOW are identical
    assert isinstance(cells["from_year"], int) and cells["from_year"] >= 1
    assert cells["to_year"] == cells["from_year"]
    # AGE month total is at least whole-years * 12
    assert cells["from_month"] >= 12 * cells["from_year"]
    # unknown units and single-char "d" are rejected (blank), matching DATETIME_DIFF's set
    assert cells["from_foo"] in (None, "")
    assert cells["from_d"] in (None, "")


async def test_datetime_format_matches_oracle(client):
    # @DATE@ = 2024-06-15 (a Saturday, UTC midnight)
    cells = await _compute(client, {
        "default": "DATETIME_FORMAT(@DATE@)",
        "long": 'DATETIME_FORMAT(@DATE@, "MMMM D, YYYY")',
        "abbr": 'DATETIME_FORMAT(@DATE@, "ddd, MMM DD")',
        "ordinal": 'DATETIME_FORMAT(@DATE@, "Do")',
        "localized_L": 'DATETIME_FORMAT(@DATE@, "L")',
        "names": 'DATETIME_FORMAT(@DATE@, "Month/DAY")',
    })
    assert cells["default"] == "2024-06-15"  # default format is date-only
    assert cells["long"] == "June 15, 2024"
    assert cells["abbr"] == "Sat, Jun 15"
    # ordinal "Do" is unsupported; the single-char D is adjacent to a letter -> literal
    assert cells["ordinal"] == "Do"
    assert cells["localized_L"] == "06/15/2024"  # localized token expands
    assert cells["names"] == "June/SATURDAY"
