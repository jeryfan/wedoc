"""System/computed field VO parity + read-path checks.

createdTime / lastModifiedTime / createdBy / lastModifiedBy / autoNumber must
serialize to the reference's settled field VO on both the create response and a
subsequent GET (isComputed, options.expression/formatting, autoNumber's INTEGER
dbFieldType), and their record cells must still resolve from the row's system
columns. isPending is deliberately absent: wedoc computes these synchronously.
"""

from conftest import signup as _signup

_FMT = {"date": "YYYY-MM-DD", "time": "None", "timeZone": "UTC"}

# expected field VO per type, with id/dbFieldName stripped (env-specific).
EXPECTED_VOS = {
    "createdTime": {
        "name": "Created Time",
        "unique": False,
        "cellValueType": "dateTime",
        "dbFieldType": "DATETIME",
        "type": "createdTime",
        "isComputed": True,
        "options": {"expression": "CREATED_TIME()", "formatting": _FMT},
    },
    "lastModifiedTime": {
        "name": "Last Modified Time",
        "unique": False,
        "cellValueType": "dateTime",
        "dbFieldType": "DATETIME",
        "type": "lastModifiedTime",
        "isComputed": True,
        "options": {"expression": "LAST_MODIFIED_TIME()", "formatting": _FMT},
    },
    "createdBy": {
        "name": "Created By",
        "unique": False,
        "cellValueType": "string",
        "dbFieldType": "TEXT",
        "type": "createdBy",
        "isComputed": True,
        "options": {},
    },
    "lastModifiedBy": {
        "name": "Last Modified By",
        "unique": False,
        "cellValueType": "string",
        "dbFieldType": "TEXT",
        "type": "lastModifiedBy",
        "isComputed": True,
        "options": {},
    },
    "autoNumber": {
        "name": "ID",
        "unique": False,
        "cellValueType": "number",
        "dbFieldType": "INTEGER",
        "type": "autoNumber",
        "isComputed": True,
        "options": {"expression": "AUTO_NUMBER()"},
    },
}


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    return table["id"]


def _stripped(vo):
    return {k: v for k, v in vo.items() if k not in ("id", "dbFieldName")}


async def test_system_computed_field_create_and_get_vo_matches_reference(client):
    tid = await _setup(client)
    for ftype, expected in EXPECTED_VOS.items():
        created = await _create(client, f"/api/table/{tid}/field", {"type": ftype})
        assert _stripped(created) == expected, (ftype, created)
        # isPending is never emitted for these synchronously-settled fields.
        assert "isPending" not in created, (ftype, created)

        got = (await client.get(f"/api/table/{tid}/field/{created['id']}")).json()
        assert _stripped(got) == expected, (ftype, got)
        assert "isPending" not in got, (ftype, got)


async def test_autonumber_reports_integer_db_field_type(client):
    tid = await _setup(client)
    created = await _create(client, f"/api/table/{tid}/field", {"type": "autoNumber"})
    assert created["dbFieldType"] == "INTEGER"
    assert created["cellValueType"] == "number"


async def test_system_computed_field_cells_resolve_on_record_read(client):
    tid = await _setup(client)
    created_time = await _create(client, f"/api/table/{tid}/field", {"type": "createdTime"})
    created_by = await _create(client, f"/api/table/{tid}/field", {"type": "createdBy"})
    auto_number = await _create(client, f"/api/table/{tid}/field", {"type": "autoNumber"})

    made = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}]},
    )
    rid = made["records"][0]["id"]

    vo = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()
    cells = vo["fields"]

    # createdTime cell mirrors the record's own createdTime (ISO string).
    assert cells[created_time["id"]] == vo["createdTime"]
    # createdBy cell resolves to a collaborator object keyed by the creating user.
    assert cells[created_by["id"]]["id"] == vo["createdBy"]
    # autoNumber cell mirrors the row's __auto_number counter (an int).
    assert cells[auto_number["id"]] == vo["autoNumber"]
    assert isinstance(vo["autoNumber"], int)
