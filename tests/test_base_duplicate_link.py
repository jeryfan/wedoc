"""Base duplication produces a self-contained copy of link fields + link cells.

Ports the id-remap the reference baseDuplicateService performs. A duplicated base
whose tables link to each other must point at its OWN tables/records, never back
at the source base. Sets up bases through the real services under a signed-in cls
context (there is no registered duplicate HTTP route) and asserts on the copy via
the field/record services. Scalar-only bases must still duplicate unchanged.
"""

import json
import os

os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("STORAGE_PREFIX", "http://localhost:3000")
os.environ.setdefault("BACKEND_CACHE_REDIS_URI", "redis://default:wedoc@127.0.0.1:46380")
os.environ.setdefault("PRISMA_DATABASE_URL", "postgresql://wedoc:wedoc@127.0.0.1:42346/wedoc")
os.environ.setdefault("SECRET_KEY", "refSecretKey000000")

from types import SimpleNamespace

from wedoc.core import cls
from wedoc.db.provider import drop_schema_sql
from wedoc.modules.base.service import BaseService
from wedoc.modules.field.schemas import FieldCreateBody
from wedoc.modules.field.service import FieldService
from wedoc.modules.record.schemas import RecordBulkPatchBody
from wedoc.modules.record.service import RecordService
from wedoc.modules.table.service import TableService

# `client` / `db` fixtures are auto-registered from conftest.py


def _sfx() -> str:
    return os.urandom(5).hex()


async def _user_space(db):
    user_id = f"usr{_sfx()}"
    space_id = f"spc{_sfx()}"
    await db.execute(
        "INSERT INTO users (id, name, email) VALUES ($1,$2,$3)",
        user_id, "Owner", f"{user_id}@example.com",
    )
    await db.execute(
        "INSERT INTO space (id, name, created_by) VALUES ($1,$2,$3)",
        space_id, "S", user_id,
    )
    return user_id, space_id


def _table_body(name, fields, records):
    return SimpleNamespace(
        name=name, fields=fields, views=None, records=records,
        fieldKeyType=None, dbTableName=None,
    )


def _by_primary(records):
    # map a table's records to {primary_cell_text: record_id}; the primary is the
    # only scalar field in these fixtures, so its value uniquely identifies a row.
    out = {}
    for r in records:
        scalar = next((v for v in r["fields"].values() if isinstance(v, str)), None)
        out[scalar] = r["id"]
    return out


def _link_ids(cell):
    items = cell if isinstance(cell, list) else [cell]
    return [i["id"] for i in items]


async def test_duplicate_base_remaps_link_fields_and_cells(client, db):
    user_id, space_id = await _user_space(db)
    token = cls.enter({"user": {"id": user_id}, "id": f"req-{_sfx()}"})
    bases = []
    try:
        tables = TableService()
        fields = FieldService()
        records = RecordService()

        base = await BaseService().create_base(space_id, "Src", None)
        bases.append(base["id"])
        # T2 is created first so it is the link target; T1 links into it (manyOne,
        # two-way) so a symmetric oneMany field is auto-generated on T2.
        t2 = await tables.create_table(
            base["id"],
            _table_body("T2", [{"name": "Name", "type": "singleLineText"}],
                        [{"fields": {"Name": "Alpha"}}, {"fields": {"Name": "Beta"}}]),
        )
        t1 = await tables.create_table(
            base["id"],
            _table_body("T1", [{"name": "Title", "type": "singleLineText"}],
                        [{"fields": {"Title": "One"}}, {"fields": {"Title": "Two"}}]),
        )
        link = await fields.create_field(
            t1["id"],
            FieldCreateBody.zod_validate(
                {"type": "link", "name": "LinkToT2",
                 "options": {"relationship": "manyOne", "foreignTableId": t2["id"]}}),
        )
        src_sym_id = link["options"]["symmetricFieldId"]
        src_sym_name = (await fields.get_field(t2["id"], src_sym_id))["name"]

        src_t2 = _by_primary(t2["records"])
        src_t1 = _by_primary(t1["records"])
        await records.update_records(
            t1["id"], [src_t1["One"], src_t1["Two"]],
            RecordBulkPatchBody.zod_validate(
                {"fieldKeyType": "id", "records": [
                    {"id": src_t1["One"], "fields": {link["id"]: {"id": src_t2["Alpha"]}}},
                    {"id": src_t1["Two"], "fields": {link["id"]: {"id": src_t2["Beta"]}}}]}),
        )

        dup = await BaseService().duplicate_base_impl(base["id"], space_id, True, "Copy")
        bases.append(dup["id"])
        assert dup["id"] != base["id"]

        new_tables = {t["name"]: t for t in await tables.list_tables(dup["id"])}
        nt1, nt2 = new_tables["T1"], new_tables["T2"]
        assert nt1["id"] != t1["id"] and nt2["id"] != t2["id"]

        nt1_fields = await fields.list_fields(nt1["id"])
        new_link = next(f for f in nt1_fields if f["type"] == "link")
        opts = new_link["options"]
        # the copied link points at the NEW target table, not the source's.
        assert opts["foreignTableId"] == nt2["id"]
        assert opts["foreignTableId"] != t2["id"]
        # symmetric/fk refs live in the new base, not the source base.
        assert opts["symmetricFieldId"] != src_sym_id
        assert opts["fkHostTableName"].startswith(dup["id"] + ".")
        assert base["id"] not in opts["fkHostTableName"]

        # the auto-generated symmetric field exists on the new T2, points back at
        # the new T1, and keeps the source symmetric field's name.
        nt2_fields = await fields.list_fields(nt2["id"])
        new_sym = next(f for f in nt2_fields if f["type"] == "link")
        assert new_sym["id"] == opts["symmetricFieldId"]
        assert new_sym["options"]["foreignTableId"] == nt1["id"]
        assert new_sym["name"] == src_sym_name

        # a copied T1 record links to the NEW T2 record (via record_map), not source.
        nt1_recs = (await records.list_records(nt1["id"], field_key_type="id", take=100))["records"]
        nt2_recs = (await records.list_records(nt2["id"], field_key_type="id", take=100))["records"]
        nt2_primary_id = next(f["id"] for f in nt2_fields if f.get("isPrimary"))
        nt2_by_text = {r["fields"][nt2_primary_id]: r["id"] for r in nt2_recs}
        nt1_primary_id = next(f["id"] for f in nt1_fields if f.get("isPrimary"))
        nt1_by_text = {r["fields"][nt1_primary_id]: r for r in nt1_recs}

        one_cell = nt1_by_text["One"]["fields"][new_link["id"]]
        assert _link_ids(one_cell) == [nt2_by_text["Alpha"]]
        # the referenced id is a NEW-base record, never the source record id.
        assert nt2_by_text["Alpha"] != src_t2["Alpha"]
        assert src_t2["Alpha"] not in _link_ids(one_cell)

        # the source base is untouched: its link cell still points at its own record.
        src_one = (await records.get_record(t1["id"], src_t1["One"], "id"))["fields"][link["id"]]
        assert _link_ids(src_one) == [src_t2["Alpha"]]
    finally:
        for b in bases:
            await db.execute(drop_schema_sql(b))
        cls.exit(token)


async def test_duplicate_base_remaps_many_many_link_cells(client, db):
    user_id, space_id = await _user_space(db)
    token = cls.enter({"user": {"id": user_id}, "id": f"req-{_sfx()}"})
    bases = []
    try:
        tables = TableService()
        fields = FieldService()
        records = RecordService()

        base = await BaseService().create_base(space_id, "Src", None)
        bases.append(base["id"])
        tags = await tables.create_table(
            base["id"],
            _table_body("Tags", [{"name": "Tag", "type": "singleLineText"}],
                        [{"fields": {"Tag": "red"}}, {"fields": {"Tag": "blue"}}]),
        )
        items = await tables.create_table(
            base["id"],
            _table_body("Items", [{"name": "Item", "type": "singleLineText"}],
                        [{"fields": {"Item": "shirt"}}, {"fields": {"Item": "hat"}}]),
        )
        link = await fields.create_field(
            items["id"],
            FieldCreateBody.zod_validate(
                {"type": "link", "name": "ItemTags",
                 "options": {"relationship": "manyMany", "foreignTableId": tags["id"]}}),
        )
        src_tag = _by_primary(tags["records"])
        src_item = _by_primary(items["records"])
        await records.update_records(
            items["id"], [src_item["shirt"], src_item["hat"]],
            RecordBulkPatchBody.zod_validate(
                {"fieldKeyType": "id", "records": [
                    {"id": src_item["shirt"],
                     "fields": {link["id"]: [{"id": src_tag["red"]}, {"id": src_tag["blue"]}]}},
                    {"id": src_item["hat"], "fields": {link["id"]: [{"id": src_tag["red"]}]}}]}),
        )

        dup = await BaseService().duplicate_base_impl(base["id"], space_id, True, "Copy")
        bases.append(dup["id"])

        new_tables = {t["name"]: t for t in await tables.list_tables(dup["id"])}
        n_items, n_tags = new_tables["Items"], new_tables["Tags"]
        n_item_fields = await fields.list_fields(n_items["id"])
        new_link = next(f for f in n_item_fields if f["type"] == "link")
        assert new_link["options"]["foreignTableId"] == n_tags["id"]
        # manyMany stores its relation in a junction table inside the new base.
        assert new_link["options"]["fkHostTableName"].startswith(dup["id"] + ".")

        n_item_recs = (
            await records.list_records(n_items["id"], field_key_type="id", take=100)
        )["records"]
        n_tag_recs = (
            await records.list_records(n_tags["id"], field_key_type="id", take=100)
        )["records"]
        item_primary = next(f["id"] for f in n_item_fields if f.get("isPrimary"))
        tag_fields = await fields.list_fields(n_tags["id"])
        tag_primary = next(f["id"] for f in tag_fields if f.get("isPrimary"))
        tag_by_text = {r["fields"][tag_primary]: r["id"] for r in n_tag_recs}
        item_by_text = {r["fields"][item_primary]: r for r in n_item_recs}

        shirt_cell = item_by_text["shirt"]["fields"][new_link["id"]]
        assert set(_link_ids(shirt_cell)) == {tag_by_text["red"], tag_by_text["blue"]}
        # remapped to the new base's tag records, none of the source ids leak in.
        assert src_tag["red"] not in _link_ids(shirt_cell)
        assert src_tag["blue"] not in _link_ids(shirt_cell)
    finally:
        for b in bases:
            await db.execute(drop_schema_sql(b))
        cls.exit(token)


async def test_duplicate_base_scalar_only_unchanged(client, db):
    user_id, space_id = await _user_space(db)
    token = cls.enter({"user": {"id": user_id}, "id": f"req-{_sfx()}"})
    bases = []
    try:
        tables = TableService()
        fields = FieldService()
        records = RecordService()

        base = await BaseService().create_base(space_id, "Src", None)
        bases.append(base["id"])
        t = await tables.create_table(
            base["id"],
            _table_body(
                "Data",
                [{"name": "Title", "type": "singleLineText"},
                 {"name": "Count", "type": "number"}],
                [{"fields": {"Title": "a", "Count": 1}},
                 {"fields": {"Title": "b", "Count": 2}}],
            ),
        )
        assert len(t["records"]) == 2

        dup = await BaseService().duplicate_base_impl(base["id"], space_id, True, "Copy")
        bases.append(dup["id"])

        new_tables = await tables.list_tables(dup["id"])
        assert [x["name"] for x in new_tables] == ["Data"]
        new_table_id = new_tables[0]["id"]
        new_fields = await fields.list_fields(new_table_id)
        assert {f["name"] for f in new_fields} == {"Title", "Count"}
        assert all(f["type"] != "link" for f in new_fields)

        new_recs = (
            await records.list_records(new_table_id, field_key_type="name", take=100)
        )["records"]
        assert {r["fields"]["Title"] for r in new_recs} == {"a", "b"}
        assert {r["fields"]["Count"] for r in new_recs} == {1, 2}

        # without records the copy provisions the schema but seeds no rows.
        dup_empty = await BaseService().duplicate_base_impl(base["id"], space_id, False, "Empty")
        bases.append(dup_empty["id"])
        empty_table_id = (await tables.list_tables(dup_empty["id"]))[0]["id"]
        empty_recs = (
            await records.list_records(empty_table_id, field_key_type="name", take=100)
        )["records"]
        assert empty_recs == []
    finally:
        for b in bases:
            await db.execute(drop_schema_sql(b))
        cls.exit(token)


async def test_duplicate_base_remaps_computed_fields(client, db):
    user_id, space_id = await _user_space(db)
    token = cls.enter({"user": {"id": user_id}, "id": f"req-{_sfx()}"})
    bases = []
    try:
        tables = TableService()
        fields = FieldService()
        records = RecordService()

        base = await BaseService().create_base(space_id, "Src", None)
        bases.append(base["id"])
        t2 = await tables.create_table(
            base["id"],
            _table_body("T2", [{"name": "Name", "type": "singleLineText"}],
                        [{"fields": {"Name": "Alpha"}}, {"fields": {"Name": "Beta"}}]),
        )
        t1 = await tables.create_table(
            base["id"],
            _table_body("T1", [{"name": "Title", "type": "singleLineText"}],
                        [{"fields": {"Title": "One"}}, {"fields": {"Title": "Two"}}]),
        )
        t2_name = next(f["id"] for f in await fields.list_fields(t2["id"]) if f.get("isPrimary"))
        t1_title = next(f["id"] for f in await fields.list_fields(t1["id"]) if f.get("isPrimary"))
        link = await fields.create_field(t1["id"], FieldCreateBody.zod_validate(
            {"type": "link", "name": "LinkToT2",
             "options": {"relationship": "manyOne", "foreignTableId": t2["id"]}}))
        # lookup T2's primary through the link, a countall rollup over the link, and
        # a formula on the local primary — each carries refs that must remap.
        await fields.create_field(t1["id"], FieldCreateBody.zod_validate(
            {"type": "singleLineText", "name": "T2Name", "isLookup": True,
             "lookupOptions": {"linkFieldId": link["id"], "lookupFieldId": t2_name,
                               "foreignTableId": t2["id"]}}))
        await fields.create_field(t1["id"], FieldCreateBody.zod_validate(
            {"type": "rollup", "name": "Cnt", "options": {"expression": "countall({values})"},
             "lookupOptions": {"linkFieldId": link["id"], "lookupFieldId": t2_name,
                               "foreignTableId": t2["id"]}}))
        await fields.create_field(t1["id"], FieldCreateBody.zod_validate(
            {"type": "formula", "name": "Bang",
             "options": {"expression": f'{{{t1_title}}} & "!"'}}))
        src_t2 = _by_primary(t2["records"])
        src_t1 = _by_primary(t1["records"])
        await records.update_records(
            t1["id"], [src_t1["One"], src_t1["Two"]],
            RecordBulkPatchBody.zod_validate(
                {"fieldKeyType": "id", "records": [
                    {"id": src_t1["One"], "fields": {link["id"]: {"id": src_t2["Alpha"]}}},
                    {"id": src_t1["Two"], "fields": {link["id"]: {"id": src_t2["Beta"]}}}]}),
        )

        dup = await BaseService().duplicate_base_impl(base["id"], space_id, True, "Copy")
        bases.append(dup["id"])

        new_tables = {t["name"]: t for t in await tables.list_tables(dup["id"])}
        nt1, nt2 = new_tables["T1"], new_tables["T2"]
        nt1_fields = await fields.list_fields(nt1["id"])
        by_name = {f["name"]: f for f in nt1_fields}
        n_link, n_lookup, n_rollup, n_formula = (
            by_name["LinkToT2"], by_name["T2Name"], by_name["Cnt"], by_name["Bang"],
        )
        n_title_id = next(f["id"] for f in nt1_fields if f.get("isPrimary"))
        n_t2_name_id = next(
            f["id"] for f in await fields.list_fields(nt2["id"]) if f.get("isPrimary")
        )
        # lookup + rollup lookupOptions point at the NEW link/table/foreign-field.
        assert n_lookup["isLookup"] is True
        assert n_lookup["lookupOptions"]["linkFieldId"] == n_link["id"]
        assert n_lookup["lookupOptions"]["foreignTableId"] == nt2["id"]
        assert n_lookup["lookupOptions"]["lookupFieldId"] == n_t2_name_id
        assert link["id"] not in n_lookup["lookupOptions"].values()
        assert t2["id"] != nt2["id"]
        assert n_rollup["lookupOptions"]["linkFieldId"] == n_link["id"]
        assert n_rollup["lookupOptions"]["foreignTableId"] == nt2["id"]
        # rollup expression is over {values}, copied verbatim.
        assert n_rollup["options"]["expression"] == "countall({values})"
        # formula expression references the NEW local field id, not the source's.
        assert n_formula["options"]["expression"] == f'{{{n_title_id}}} & "!"'
        assert t1_title not in n_formula["options"]["expression"]

        # computed cells read correctly on a copied record.
        nt1_recs = (
            await records.list_records(nt1["id"], field_key_type="id", take=100)
        )["records"]
        by_title = {r["fields"][n_title_id]: r["fields"] for r in nt1_recs}
        assert by_title["One"][n_lookup["id"]] == "Alpha"
        assert by_title["One"][n_rollup["id"]] == 1
        assert by_title["One"][n_formula["id"]] == "One!"
        assert by_title["Two"][n_formula["id"]] == "Two!"
    finally:
        for b in bases:
            await db.execute(drop_schema_sql(b))
        cls.exit(token)


async def test_create_base_from_template_apply_into_existing(client, db):
    user_id, space_id = await _user_space(db)
    token = cls.enter({"user": {"id": user_id}, "id": f"req-{_sfx()}"})
    template_id = f"tpl{_sfx()}"
    bases = []
    try:
        tables = TableService()
        fields = FieldService()
        records = RecordService()

        # source base the template snapshots: T1 -> T2 manyOne link with records.
        src = await BaseService().create_base(space_id, "TemplateSrc", None)
        bases.append(src["id"])
        t2 = await tables.create_table(src["id"], _table_body(
            "T2", [{"name": "Name", "type": "singleLineText"}],
            [{"fields": {"Name": "Alpha"}}, {"fields": {"Name": "Beta"}}]))
        t1 = await tables.create_table(src["id"], _table_body(
            "T1", [{"name": "Title", "type": "singleLineText"}],
            [{"fields": {"Title": "One"}}, {"fields": {"Title": "Two"}}]))
        link = await fields.create_field(t1["id"], FieldCreateBody.zod_validate(
            {"type": "link", "name": "LinkToT2",
             "options": {"relationship": "manyOne", "foreignTableId": t2["id"]}}))
        s2, s1 = _by_primary(t2["records"]), _by_primary(t1["records"])
        await records.update_records(t1["id"], [s1["One"], s1["Two"]],
            RecordBulkPatchBody.zod_validate({"fieldKeyType": "id", "records": [
                {"id": s1["One"], "fields": {link["id"]: {"id": s2["Alpha"]}}},
                {"id": s1["Two"], "fields": {link["id"]: {"id": s2["Beta"]}}}]}))
        await db.execute(
            'INSERT INTO template (id, base_id, name, snapshot, "order", created_by, '
            "usage_count) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            template_id, src["id"], "Tmpl", json.dumps({"baseId": src["id"]}), 1.0, user_id, 0)
        # existing target base already holds a table; apply must ADD, not clear it.
        target = await BaseService().create_base(space_id, "Target", None)
        bases.append(target["id"])
        await tables.create_table(target["id"], _table_body(
            "Existing", [{"name": "K", "type": "singleLineText"}],
            [{"fields": {"K": "keep"}}]))
        assert {t["name"] for t in await tables.list_tables(target["id"])} == {"Existing"}

        result = await BaseService().create_base_from_template(
            space_id, template_id, True, target["id"])
        # return reflects the target base, not a freshly created one.
        assert result["id"] == target["id"]
        assert result["spaceId"] == space_id
        assert result["name"] == "Target"
        after = {t["name"]: t for t in await tables.list_tables(target["id"])}
        # ADD semantics: pre-existing table kept, template tables added.
        assert set(after) == {"Existing", "T1", "T2"}
        keep = (
            await records.list_records(after["Existing"]["id"], field_key_type="name", take=100)
        )["records"]
        assert [r["fields"]["K"] for r in keep] == ["keep"]

        # the added link + its cells point at the TARGET base's new ids, not source.
        nt1_fields = await fields.list_fields(after["T1"]["id"])
        n_link = next(f for f in nt1_fields if f["type"] == "link")
        assert n_link["options"]["foreignTableId"] == after["T2"]["id"]
        assert n_link["options"]["foreignTableId"] != t2["id"]
        assert n_link["options"]["fkHostTableName"].startswith(target["id"] + ".")

        n_title = next(f["id"] for f in nt1_fields if f.get("isPrimary"))
        nt1_recs = (
            await records.list_records(after["T1"]["id"], field_key_type="id", take=100)
        )["records"]
        nt2_recs = (
            await records.list_records(after["T2"]["id"], field_key_type="id", take=100)
        )["records"]
        nt2_primary = next(
            f["id"] for f in await fields.list_fields(after["T2"]["id"]) if f.get("isPrimary")
        )
        nt2_by_text = {r["fields"][nt2_primary]: r["id"] for r in nt2_recs}
        by_title = {r["fields"][n_title]: r for r in nt1_recs}
        one_cell = by_title["One"]["fields"][n_link["id"]]
        assert _link_ids(one_cell) == [nt2_by_text["Alpha"]]
        assert s2["Alpha"] not in _link_ids(one_cell)

        # usageCount is bumped for the apply-into-existing branch too.
        row = await db.fetchrow("SELECT usage_count FROM template WHERE id = $1", template_id)
        assert row["usage_count"] == 1
    finally:
        for b in bases:
            await db.execute(drop_schema_sql(b))
        await db.execute("DELETE FROM template WHERE id = $1", template_id)
        cls.exit(token)
