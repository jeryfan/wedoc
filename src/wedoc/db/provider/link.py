"""Link relation DDL: foreign-key columns and junction tables.

Ports the postgres db-provider link artifacts
(`create-database-column-field-visitor.postgres.ts` + field-supplement option
derivation): ManyOne/OneOne store the foreign key on the host row, OneMany
(two-way) stores the self key on the foreign row, and ManyMany / one-way
OneMany use a junction table. The denormalized ``{id,title}`` cell always lives
in the base JSONB column created separately via ``add_field_column_sql``.
"""


def parse_db_table_name(name: str) -> tuple[str, str]:
    """Split a stored ``dbTableName`` (``schema.table``) into its parts."""
    schema, _, table = name.partition(".")
    return schema, table


def _q(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def foreign_key_name(field_id: str | None) -> str:
    if not field_id:
        from ...core.ids import random_string

        return f"__fk_rad{random_string(16)}"
    return f"__fk_{field_id}"


def junction_table_name(field_id: str, symmetric_field_id: str | None) -> str:
    return (
        f"junction_{field_id}_{symmetric_field_id}"
        if symmetric_field_id
        else f"junction_{field_id}"
    )


def _fk_column_ddl(
    schema: str,
    host_table: str,
    fk_col: str,
    ref_schema: str,
    ref_table: str,
    *,
    on_delete: str = "SET NULL",
    with_order: bool = True,
    unique: bool = False,
) -> list[str]:
    host = _q(schema, host_table)
    stmts = [
        f'ALTER TABLE {host} ADD COLUMN "{fk_col}" text NULL',
        f'ALTER TABLE {host} ADD CONSTRAINT "fk_{fk_col}" '
        f'FOREIGN KEY ("{fk_col}") REFERENCES {_q(ref_schema, ref_table)}("__id") '
        f"ON DELETE {on_delete}",
    ]
    if unique:
        stmts.append(f'ALTER TABLE {host} ADD CONSTRAINT "index_{fk_col}" UNIQUE ("{fk_col}")')
    if with_order:
        stmts.append(f'ALTER TABLE {host} ADD COLUMN "{fk_col}_order" double precision NULL')
    return stmts


def _junction_ddl(
    schema: str,
    junction_table: str,
    self_key: str,
    foreign_key: str,
    self_schema: str,
    self_table: str,
    foreign_schema: str,
    foreign_table: str,
    *,
    with_order: bool = True,
) -> list[str]:
    cols = [
        '"__id" serial PRIMARY KEY',
        f'"{self_key}" text NULL',
        f'"{foreign_key}" text NULL',
    ]
    if with_order:
        cols.append('"__order" double precision NULL')
    cols.append(
        f'CONSTRAINT "fk_{self_key}" FOREIGN KEY ("{self_key}") '
        f'REFERENCES {_q(self_schema, self_table)}("__id") ON DELETE CASCADE'
    )
    cols.append(
        f'CONSTRAINT "fk_{foreign_key}" FOREIGN KEY ("{foreign_key}") '
        f'REFERENCES {_q(foreign_schema, foreign_table)}("__id") ON DELETE CASCADE'
    )
    cols.append(
        f'CONSTRAINT "uniq_{self_key}_{foreign_key}" UNIQUE ("{self_key}", "{foreign_key}")'
    )
    return [f'CREATE TABLE {_q(schema, junction_table)} ({", ".join(cols)})']


def link_relation_ddl(
    options: dict,
    self_db_table_name: str,
    foreign_db_table_name: str,
) -> list[str]:
    """Foreign-key/junction DDL for the *non-symmetric* side of a link field.

    ``self_db_table_name`` / ``foreign_db_table_name`` are the stored
    ``schema.table`` identifiers of the current and foreign tables.
    """
    relationship = options["relationship"]
    is_one_way = bool(options.get("isOneWay"))
    self_schema, self_table = parse_db_table_name(self_db_table_name)
    foreign_schema, foreign_table = parse_db_table_name(foreign_db_table_name)
    fk_schema, fk_table = parse_db_table_name(options["fkHostTableName"])
    self_key = options["selfKeyName"]
    foreign_key = options["foreignKeyName"]

    if relationship == "manyOne":
        return _fk_column_ddl(
            fk_schema, fk_table, foreign_key, foreign_schema, foreign_table
        )
    if relationship == "oneOne":
        return _fk_column_ddl(
            fk_schema, fk_table, foreign_key, foreign_schema, foreign_table, unique=True
        )
    if relationship == "oneMany":
        if is_one_way:
            return _junction_ddl(
                fk_schema, fk_table, self_key, foreign_key,
                self_schema, self_table, foreign_schema, foreign_table,
                with_order=False,
            )
        # two-way: self key lives on the foreign (many) table.
        return _fk_column_ddl(
            fk_schema, fk_table, self_key, self_schema, self_table
        )
    if relationship == "manyMany":
        return _junction_ddl(
            fk_schema, fk_table, self_key, foreign_key,
            self_schema, self_table, foreign_schema, foreign_table,
        )
    raise ValueError(f"Unsupported relationship: {relationship}")


def link_relation_teardown_ddl(options: dict) -> list[str]:
    """Inverse of ``link_relation_ddl``: drop the FK column / junction table.

    Used when a link field is converted away to a scalar type. ``fkHostTableName``
    is the junction table for junction relationships and the FK-owning data table
    otherwise. Dropping the column also drops its FK/unique constraints and the
    order column; dropping the junction table removes the whole relation.
    """
    relationship = options["relationship"]
    is_one_way = bool(options.get("isOneWay"))
    fk_schema, fk_table = parse_db_table_name(options["fkHostTableName"])
    host = _q(fk_schema, fk_table)
    if relationship == "manyMany" or (relationship == "oneMany" and is_one_way):
        return [f"DROP TABLE IF EXISTS {host}"]
    key = options["selfKeyName"] if relationship == "oneMany" else options["foreignKeyName"]
    return [
        f'ALTER TABLE {host} DROP COLUMN IF EXISTS "{key}"',
        f'ALTER TABLE {host} DROP COLUMN IF EXISTS "{key}_order"',
    ]
