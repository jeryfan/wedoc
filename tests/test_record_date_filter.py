"""date-field filtering parity (record list / view / conditionalRollup share one
filter compiler). Ports the reference single-value datetime cell-value filter: a
``{mode, ...}`` value resolves to a ``[start, end]`` timestamptz range and each
operator maps to a BETWEEN / boundary comparison. Deterministic in UTC (the date
field's default formatting timezone is UTC).
"""

import json
from datetime import UTC, datetime, timedelta

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _flt(field_id: str, operator: str, value):
    return {
        "conjunction": "and",
        "filterSet": [{"fieldId": field_id, "operator": operator, "value": value}],
    }


def _wv(mode: str, **kw):
    return {"mode": mode, "timeZone": "UTC", **kw}


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    # wedoc defaults a date field's formatting.timeZone to UTC, which is what the
    # range resolver reads — keeping this deterministic without pinning options.
    await _create(client, f"/api/table/{tid}/field", {"type": "date", "name": "D"})
    field_rows = (await client.get(f"/api/table/{tid}/field")).json()
    ids = {f["name"]: f["id"] for f in field_rows}
    name_id, d_id = ids["Name"], ids["D"]

    now = datetime.now(UTC)

    def at_noon(day: datetime) -> str:
        return _iso(day.replace(hour=12, minute=0, second=0, microsecond=0))

    seeded = {
        "yst": at_noon(now - timedelta(days=1)),
        "tod": at_noon(now),
        "tmr": at_noon(now + timedelta(days=1)),
        "lastweek": at_noon(now - timedelta(days=7)),
        "nextweek": at_noon(now + timedelta(days=7)),
        "fixed2020": "2020-06-15T12:00:00.000Z",
    }
    records = [{"fields": {name_id: lbl, d_id: iso}} for lbl, iso in seeded.items()]
    records.append({"fields": {name_id: "empty"}})  # null date cell
    await _create(
        client, f"/api/table/{tid}/record", {"fieldKeyType": "id", "records": records}
    )
    return tid, name_id, d_id


async def _labels(client, tid, name_id, flt) -> list[str]:
    resp = await client.get(
        f"/api/table/{tid}/record",
        params={"fieldKeyType": "id", "filter": json.dumps(flt)},
    )
    assert resp.status_code == 200, resp.text
    return sorted(
        r["fields"][name_id] for r in resp.json()["records"] if name_id in r["fields"]
    )


async def test_iswithin_relative_days(client):
    tid, name_id, d_id = await _setup(client)
    assert await _labels(client, tid, name_id, _flt(d_id, "isWithIn", _wv("today"))) == ["tod"]
    assert await _labels(client, tid, name_id, _flt(d_id, "isWithIn", _wv("yesterday"))) == ["yst"]
    assert await _labels(client, tid, name_id, _flt(d_id, "isWithIn", _wv("tomorrow"))) == ["tmr"]


async def test_boundary_operators(client):
    tid, name_id, d_id = await _setup(client)
    assert await _labels(client, tid, name_id, _flt(d_id, "isBefore", _wv("today"))) == sorted(
        ["yst", "lastweek", "fixed2020"]
    )
    assert await _labels(client, tid, name_id, _flt(d_id, "isAfter", _wv("today"))) == sorted(
        ["tmr", "nextweek"]
    )
    assert await _labels(client, tid, name_id, _flt(d_id, "isOnOrAfter", _wv("today"))) == sorted(
        ["tod", "tmr", "nextweek"]
    )
    assert await _labels(client, tid, name_id, _flt(d_id, "isOnOrBefore", _wv("today"))) == sorted(
        ["yst", "lastweek", "fixed2020", "tod"]
    )


async def test_exact_date_and_isnot_and_range(client):
    tid, name_id, d_id = await _setup(client)
    exact = _wv("exactDate", exactDate="2020-06-15T00:00:00.000Z")
    assert await _labels(client, tid, name_id, _flt(d_id, "is", exact)) == ["fixed2020"]
    # isNot compiles to (NOT BETWEEN ... OR IS NULL), so the null row is included.
    assert await _labels(client, tid, name_id, _flt(d_id, "isNot", exact)) == sorted(
        ["yst", "tod", "tmr", "lastweek", "nextweek", "empty"]
    )
    rng = _wv(
        "dateRange",
        exactDate="2020-06-01T00:00:00.000Z",
        exactDateEnd="2020-06-30T00:00:00.000Z",
    )
    assert await _labels(client, tid, name_id, _flt(d_id, "isWithIn", rng)) == ["fixed2020"]


async def test_empty_and_current_week(client):
    tid, name_id, d_id = await _setup(client)
    assert await _labels(client, tid, name_id, _flt(d_id, "isEmpty", None)) == ["empty"]
    assert await _labels(client, tid, name_id, _flt(d_id, "isNotEmpty", None)) == sorted(
        ["yst", "tod", "tmr", "lastweek", "nextweek", "fixed2020"]
    )
    # current Mon-Sun week: today is always inside it; a week back/ahead and 2020
    # never are, regardless of which weekday the suite runs on.
    cw = await _labels(client, tid, name_id, _flt(d_id, "isWithIn", _wv("currentWeek")))
    assert "tod" in cw
    assert "lastweek" not in cw and "nextweek" not in cw
    assert "fixed2020" not in cw and "empty" not in cw
