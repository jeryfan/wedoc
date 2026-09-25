"""Link field option derivation — ports field-supplement.service link helpers.

Pure helpers (no DB access): the service resolves the foreign table's
``dbTableName``/primary field and passes them in.
"""

from typing import Any

from ...db import provider as ddl

RELATIONSHIP_REVERT = {
    "oneMany": "manyOne",
    "manyOne": "oneMany",
    "manyMany": "manyMany",
    "oneOne": "oneOne",
}
RELATIONSHIPS = frozenset(RELATIONSHIP_REVERT)


def is_multi_value_link(relationship: str) -> bool:
    return relationship in ("manyMany", "oneMany")


def build_link_options(
    *,
    options_ro: dict[str, Any],
    field_id: str,
    symmetric_field_id: str | None,
    lookup_field_id: str,
    self_db_table_name: str,
    foreign_db_table_name: str,
    base_id: str | None,
) -> dict[str, Any]:
    """Full link options VO (ports generateLinkOptionsVo)."""
    relationship = options_ro["relationship"]
    common: dict[str, Any] = {
        "relationship": relationship,
        "foreignTableId": options_ro["foreignTableId"],
        "lookupFieldId": lookup_field_id,
        "isOneWay": bool(options_ro.get("isOneWay")),
    }
    if base_id is not None:
        common["baseId"] = base_id
    for optional in ("filterByViewId", "visibleFieldIds", "filter"):
        if options_ro.get(optional) is not None:
            common[optional] = options_ro[optional]

    if relationship == "manyMany":
        fk_host = _junction_host(base_id, self_db_table_name, field_id, symmetric_field_id)
        common.update(
            fkHostTableName=fk_host,
            selfKeyName=ddl.foreign_key_name(symmetric_field_id),
            foreignKeyName=ddl.foreign_key_name(field_id),
        )
    elif relationship == "manyOne":
        common.update(
            fkHostTableName=self_db_table_name,
            selfKeyName="__id",
            foreignKeyName=ddl.foreign_key_name(field_id),
        )
    elif relationship == "oneMany":
        is_one_way = bool(options_ro.get("isOneWay"))
        common.update(
            fkHostTableName=(
                _junction_host(base_id, self_db_table_name, field_id, symmetric_field_id)
                if is_one_way
                else foreign_db_table_name
            ),
            selfKeyName=ddl.foreign_key_name(symmetric_field_id),
            foreignKeyName=ddl.foreign_key_name(field_id) if is_one_way else "__id",
        )
    elif relationship == "oneOne":
        common.update(
            fkHostTableName=self_db_table_name,
            selfKeyName="__id",
            foreignKeyName=ddl.foreign_key_name(field_id),
        )
    else:  # pragma: no cover - guarded before call
        raise ValueError(f"relationship is invalid: {relationship}")
    if symmetric_field_id is not None:
        common["symmetricFieldId"] = symmetric_field_id
    return common


def build_symmetric_options(
    main_options: dict[str, Any],
    self_table_id: str,
    self_lookup_field_id: str,
    main_field_id: str,
) -> dict[str, Any]:
    """Reverse link options on the foreign table (ports generateSymmetricField)."""
    reverted = RELATIONSHIP_REVERT[main_options["relationship"]]
    options: dict[str, Any] = {
        "relationship": reverted,
        "foreignTableId": self_table_id,
        "lookupFieldId": self_lookup_field_id,
        "isOneWay": False,
        "fkHostTableName": main_options["fkHostTableName"],
        "selfKeyName": main_options["foreignKeyName"],
        "foreignKeyName": main_options["selfKeyName"],
        "symmetricFieldId": main_field_id,
    }
    if main_options.get("baseId") is not None:
        options["baseId"] = main_options["baseId"]
    return options


def _junction_host(
    base_id: str | None, self_db_table_name: str, field_id: str, symmetric_field_id: str | None
) -> str:
    schema, _ = ddl.parse_db_table_name(self_db_table_name)
    return f"{schema}.{ddl.junction_table_name(field_id, symmetric_field_id)}"
