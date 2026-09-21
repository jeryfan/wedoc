"""SQLAlchemy 2.0 models for the wedoc meta (control-plane) schema.

Table/column names, nullability, server defaults, primary keys,
unique indexes and secondary indexes mirror the upstream db-main-prisma
prisma schema and the migrated PostgreSQL database exactly.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class MetaBase(DeclarativeBase):
    pass


# prisma: Space
class Space(MetaBase):
    __tablename__ = "space"
    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    avatar: Mapped[str | None] = mapped_column(sa.Text)
    credit: Mapped[int | None] = mapped_column(sa.Integer)
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    is_template: Mapped[bool | None] = mapped_column(sa.Boolean)


# prisma: DataDbConnection
class DataDbConnection(MetaBase):
    __tablename__ = "data_db_connection"
    __table_args__ = (
        sa.Index("data_db_connection_url_fingerprint_key", "url_fingerprint", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    provider: Mapped[str] = mapped_column(
        sa.Enum("postgres", name="DataDbProvider"),
        nullable=False,
        server_default=sa.text("'postgres'::\"DataDbProvider\""),
    )
    encrypted_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    url_fingerprint: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_host: Mapped[str | None] = mapped_column(sa.Text)
    display_database: Mapped[str | None] = mapped_column(sa.Text)
    internal_schema: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Enum(
            "pending",
            "validating",
            "ready",
            "error",
            "migrating",
            "disabled",
            name="DataDbConnectionStatus",
        ),
        nullable=False,
        server_default=sa.text("'pending'::\"DataDbConnectionStatus\""),
    )
    schema_version: Mapped[str | None] = mapped_column(sa.Text)
    capabilities: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    last_validated_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    health_state: Mapped[str] = mapped_column(
        sa.Enum(
            "healthy",
            "read_only",
            "unreachable",
            "degraded",
            name="DataDbConnectionHealthState",
        ),
        nullable=False,
        server_default=sa.text("'healthy'::\"DataDbConnectionHealthState\""),
    )
    health_reason: Mapped[str | None] = mapped_column(sa.Text)
    health_changed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_health_check_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    consecutive_health_failures: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: SpaceDataDbBinding
class SpaceDataDbBinding(MetaBase):
    __tablename__ = "space_data_db_binding"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["data_db_connection_id"],
            ["data_db_connection.id"],
            onupdate="CASCADE",
            ondelete="SET NULL",
            name="space_data_db_binding_data_db_connection_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="space_data_db_binding_space_id_fkey",
        ),
        sa.Index("space_data_db_binding_space_id_key", "space_id", unique=True),
        sa.Index("space_data_db_binding_data_db_connection_id_idx", "data_db_connection_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    space_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    data_db_connection_id: Mapped[str | None] = mapped_column(sa.Text)
    mode: Mapped[str] = mapped_column(
        sa.Enum("default", "byodb", name="SpaceDataDbBindingMode"),
        nullable=False,
        server_default=sa.text("'default'::\"SpaceDataDbBindingMode\""),
    )
    state: Mapped[str] = mapped_column(
        sa.Enum(
            "ready",
            "validating",
            "initializing",
            "migrating",
            "error",
            "disabled",
            name="SpaceDataDbBindingState",
        ),
        nullable=False,
        server_default=sa.text("'ready'::\"SpaceDataDbBindingState\""),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_history_flushed_at: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
    )
    last_removal_flushed_at: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
    )


# prisma: SpaceDataDbMigrationJob
class SpaceDataDbMigrationJob(MetaBase):
    __tablename__ = "space_data_db_migration_job"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["source_connection_id"],
            ["data_db_connection.id"],
            onupdate="CASCADE",
            ondelete="SET NULL",
            name="space_data_db_migration_job_source_connection_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="space_data_db_migration_job_space_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["target_connection_id"],
            ["data_db_connection.id"],
            onupdate="CASCADE",
            ondelete="SET NULL",
            name="space_data_db_migration_job_target_connection_id_fkey",
        ),
        sa.Index("space_data_db_migration_job_space_id_state_idx", "space_id", "state"),
        sa.Index("space_data_db_migration_job_source_connection_id_idx", "source_connection_id"),
        sa.Index("space_data_db_migration_job_target_connection_id_idx", "target_connection_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    space_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_connection_id: Mapped[str | None] = mapped_column(sa.Text)
    target_connection_id: Mapped[str | None] = mapped_column(sa.Text)
    target_mode: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'migrate-space'::text"),
    )
    switch_on_completion: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    )
    state: Mapped[str] = mapped_column(
        sa.Enum(
            "pending",
            "waiting_worker",
            "preflight",
            "freezing_writes",
            "copying",
            "validating",
            "switching",
            "succeeded",
            "failed",
            "canceled",
            "rolled_back",
            name="SpaceDataDbMigrationJobState",
        ),
        nullable=False,
        server_default=sa.text("'pending'::\"SpaceDataDbMigrationJobState\""),
    )
    target_url_fingerprint: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_internal_schema: Mapped[str] = mapped_column(sa.Text, nullable=False)
    inventory: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    copy_stats: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    validation_stats: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    completed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: BaseDataDbMoveJob
class BaseDataDbMoveJob(MetaBase):
    __tablename__ = "base_data_db_move_job"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["base_id"],
            ["base.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="base_data_db_move_job_base_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["source_space_id"],
            ["space.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="base_data_db_move_job_source_space_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["target_space_id"],
            ["space.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="base_data_db_move_job_target_space_id_fkey",
        ),
        sa.Index("base_data_db_move_job_base_id_state_idx", "base_id", "state"),
        sa.Index("base_data_db_move_job_state_idx", "state"),
        sa.Index("base_data_db_move_job_source_space_id_state_idx", "source_space_id", "state"),
        sa.Index("base_data_db_move_job_target_space_id_state_idx", "target_space_id", "state"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_space_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_space_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_connection_id: Mapped[str | None] = mapped_column(sa.Text)
    target_connection_id: Mapped[str | None] = mapped_column(sa.Text)
    state: Mapped[str] = mapped_column(
        sa.Enum(
            "pending",
            "waiting_worker",
            "copying_base_schema",
            "copying_shared_rows",
            "validating",
            "switching",
            "succeeded",
            "failed",
            "cancelled",
            name="BaseDataDbMoveJobState",
        ),
        nullable=False,
        server_default=sa.text("'pending'::\"BaseDataDbMoveJobState\""),
    )
    inventory: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    copy_stats: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    validation_stats: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    completed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: PinResource
class PinResource(MetaBase):
    __tablename__ = "pin_resource"
    __table_args__ = (
        sa.Index(
            "pin_resource_created_by_resource_id_key",
            "created_by",
            "resource_id",
            unique=True,
        ),
        sa.Index("pin_resource_order_idx", "order"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)


# prisma: Base
class Base(MetaBase):
    __tablename__ = "base"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
            name="base_space_id_fkey",
        ),
        sa.Index("base_order_idx", "order"),
        sa.Index("base_space_id_idx", "space_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    space_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    icon: Mapped[str | None] = mapped_column(sa.Text)
    schema_pass: Mapped[str | None] = mapped_column(sa.Text)
    v2_enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    )
    provision_state: Mapped[str] = mapped_column(
        sa.Enum("pending", "ready", "error", "deleting", name="ProvisionState"),
        nullable=False,
        server_default=sa.text("'ready'::\"ProvisionState\""),
    )
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: TableMeta
class TableMeta(MetaBase):
    __tablename__ = "table_meta"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["base_id"],
            ["base.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
            name="table_meta_base_id_fkey",
        ),
        sa.Index("table_meta_order_idx", "order"),
        sa.Index("table_meta_db_table_name_idx", "db_table_name"),
        sa.Index("table_meta_base_id_deleted_time_idx", "base_id", "deleted_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    icon: Mapped[str | None] = mapped_column(sa.Text)
    db_table_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    db_view_name: Mapped[str | None] = mapped_column(sa.Text)
    provision_state: Mapped[str] = mapped_column(
        sa.Enum("pending", "ready", "error", "deleting", name="ProvisionState"),
        nullable=False,
        server_default=sa.text("'ready'::\"ProvisionState\""),
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: TableQueryObservationShard
class TableQueryObservationShard(MetaBase):
    __tablename__ = "table_query_observation_shard"
    __table_args__ = (
        sa.PrimaryKeyConstraint(
            "table_id",
            "query_kind",
            "shape_hash",
            "window_start",
            "writer_id",
            name="table_query_observation_shard_pkey",
        ),
        sa.Index("table_query_observation_shard_window_start_idx", "window_start"),
        sa.Index("table_query_observation_shard_table_start_idx", "table_id", "window_start"),
        sa.Index(
            "table_query_observation_shard_search_activity_idx",
            "query_kind",
            "table_id",
            "window_start",
        ),
        sa.Index("table_query_observation_shard_base_start_idx", "base_id", "window_start"),
    )

    space_id: Mapped[str | None] = mapped_column(sa.Text)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    query_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    shape_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
        nullable=False,
    )
    writer_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    window_size_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    request_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    slow_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    timeout_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    db_error_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    total_duration_ms: Mapped[float] = mapped_column(sa.Double, nullable=False)
    max_duration_ms: Mapped[float] = mapped_column(sa.Double, nullable=False)
    total_db_duration_ms: Mapped[float | None] = mapped_column(sa.Double)
    max_db_duration_ms: Mapped[float | None] = mapped_column(sa.Double)
    shape: Mapped[dict] = mapped_column(postgresql.JSONB, nullable=False)
    sql_diagnostics: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
    )


# prisma: SchemaOperation
class SchemaOperation(MetaBase):
    __tablename__ = "schema_operation"
    __table_args__ = (
        sa.Index("schema_operation_idempotency_key_key", "idempotency_key", unique=True),
        sa.Index("schema_operation_status_next_run_at_idx", "status", "next_run_at"),
        sa.Index("schema_operation_resource_status_idx", "resource_type", "resource_id", "status"),
        sa.Index("schema_operation_base_status_idx", "base_id", "status"),
        sa.Index("schema_operation_table_status_idx", "table_id", "status"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phase: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str | None] = mapped_column(sa.Text)
    table_id: Mapped[str | None] = mapped_column(sa.Text)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    result: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("8"),
    )
    next_run_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=sa.text("now()"),
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
    )
    locked_by: Mapped[str | None] = mapped_column(sa.Text)
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=sa.text("now()"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
    )
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: Field
class Field(MetaBase):
    __tablename__ = "field"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["table_meta.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
            name="field_table_id_fkey",
        ),
        sa.Index("field_lookup_linked_field_id_idx", "lookup_linked_field_id"),
        sa.Index("field_table_id_deleted_time_idx", "table_id", "deleted_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    options: Mapped[str | None] = mapped_column(sa.Text)
    meta: Mapped[str | None] = mapped_column(sa.Text)
    ai_config: Mapped[str | None] = mapped_column(sa.Text)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cell_value_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_multiple_cell_value: Mapped[bool | None] = mapped_column(sa.Boolean)
    db_field_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    db_field_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    provision_state: Mapped[str] = mapped_column(
        sa.Enum("pending", "ready", "error", "deleting", name="ProvisionState"),
        nullable=False,
        server_default=sa.text("'ready'::\"ProvisionState\""),
    )
    not_null: Mapped[bool | None] = mapped_column(sa.Boolean)
    unique: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_primary: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_computed: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_lookup: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_conditional_lookup: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_pending: Mapped[bool | None] = mapped_column(sa.Boolean)
    has_error: Mapped[bool | None] = mapped_column(sa.Boolean)
    lookup_linked_field_id: Mapped[str | None] = mapped_column(sa.Text)
    lookup_options: Mapped[str | None] = mapped_column(sa.Text)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: ComputedUpdateOutbox
class ComputedUpdateOutbox(MetaBase):
    __tablename__ = "computed_update_outbox"
    __table_args__ = (
        sa.Index("computed_update_outbox_status_next_run_at_idx", "status", "next_run_at"),
        sa.Index("computed_update_outbox_base_id_seed_table_id_idx", "base_id", "seed_table_id"),
        sa.Index("computed_update_outbox_plan_hash_idx", "plan_hash"),
        sa.Index("computed_update_outbox_run_id_idx", "run_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed_table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed_record_ids: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    change_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    steps: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    edges: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("8"),
    )
    next_run_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    locked_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    locked_by: Mapped[str | None] = mapped_column(sa.Text)
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    estimated_complexity: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    plan_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dirty_stats: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    run_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    origin_run_ids: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("ARRAY[]::text[]"),
    )
    run_total_steps: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    run_completed_steps_before: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    affected_table_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    affected_field_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    sync_max_level: Mapped[int | None] = mapped_column(sa.Integer)
    source_changed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    stage_depth: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    predecessor_task_id: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)


# prisma: ComputedUpdateOutboxSeed
class ComputedUpdateOutboxSeed(MetaBase):
    __tablename__ = "computed_update_outbox_seed"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["computed_update_outbox.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="computed_update_outbox_seed_task_id_fkey",
        ),
        sa.Index(
            "computed_update_outbox_seed_task_id_table_id_record_id_key",
            "task_id",
            "table_id",
            "record_id",
            unique=True,
        ),
        sa.Index("computed_update_outbox_seed_task_id_idx", "task_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    task_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: ComputedUpdateStageLedger
class ComputedUpdateStageLedger(MetaBase):
    __tablename__ = "computed_update_stage_ledger"
    __table_args__ = (
        sa.PrimaryKeyConstraint(
            "scope_id",
            "kind",
            "table_id",
            "record_id",
            name="computed_update_stage_ledger_pkey",
        ),
        sa.CheckConstraint(
            "(kind = ANY (ARRAY['excluded'::text, 'frontier'::text, 'consumed'::text]))",
            name="computed_update_stage_ledger_kind_check",
        ),
        sa.Index("computed_update_stage_ledger_scope_id_kind_seq_idx", "scope_id", "kind", "seq"),
    )

    scope_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seq: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default=sa.text("0"))


# prisma: ComputedUpdateDeadLetter
class ComputedUpdateDeadLetter(MetaBase):
    __tablename__ = "computed_update_dead_letter"
    __table_args__ = (
        sa.Index(
            "computed_update_dead_letter_base_id_seed_table_id_idx",
            "base_id",
            "seed_table_id",
        ),
        sa.Index("computed_update_dead_letter_plan_hash_idx", "plan_hash"),
        sa.Index("computed_update_dead_letter_run_id_idx", "run_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed_table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed_record_ids: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    change_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    steps: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    edges: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("8"),
    )
    next_run_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    locked_by: Mapped[str | None] = mapped_column(sa.Text)
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    estimated_complexity: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    plan_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dirty_stats: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    run_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    origin_run_ids: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("ARRAY[]::text[]"),
    )
    run_total_steps: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    run_completed_steps_before: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    affected_table_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    affected_field_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    sync_max_level: Mapped[int | None] = mapped_column(sa.Integer)
    source_changed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    stage_depth: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    predecessor_task_id: Mapped[str | None] = mapped_column(sa.Text)
    trace_data: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    failed_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)


# prisma: ComputedUpdateRunHistory
class ComputedUpdateRunHistory(MetaBase):
    __tablename__ = "computed_update_run_history"
    __table_args__ = (
        sa.Index("computed_update_run_history_run_id_idx", "run_id"),
        sa.Index("computed_update_run_history_base_id_completed_at_idx", "base_id", "completed_at"),
        sa.Index("computed_update_run_history_completed_at_idx", "completed_at"),
        sa.Index(
            "computed_update_run_history_origin_run_ids_gin",
            "origin_run_ids",
            postgresql_using="gin",
        ),
    )

    task_id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed_table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    change_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    run_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    origin_run_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    steps: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    edges: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    affected_table_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    affected_field_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    source_field_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    seed_record_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    stage_depth: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    predecessor_task_id: Mapped[str | None] = mapped_column(sa.Text)
    run_total_steps: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    run_completed_steps_before: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    sync_max_level: Mapped[int | None] = mapped_column(sa.Integer)
    estimated_complexity: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    outcome: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_changed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    enqueued_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    completed_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
    )
    duration_ms: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )


# prisma: ComputedUpdatePauseScope
class ComputedUpdatePauseScope(MetaBase):
    __tablename__ = "computed_update_pause_scope"
    __table_args__ = (
        sa.CheckConstraint(
            "(scope_type = ANY (ARRAY['space'::text, 'base'::text, 'table'::text]))",
            name="computed_update_pause_scope_scope_type_check",
        ),
        sa.Index(
            "computed_update_pause_scope_scope_type_scope_id_key",
            "scope_type",
            "scope_id",
            unique=True,
        ),
        sa.Index("computed_update_pause_scope_resume_at_idx", "resume_at"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    scope_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    paused_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    paused_by: Mapped[str | None] = mapped_column(sa.Text)
    resume_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    reason: Mapped[str | None] = mapped_column(sa.Text)
    updated_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    updated_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: ComputedFieldActivity
class ComputedFieldActivity(MetaBase):
    __tablename__ = "computed_field_activity"
    __table_args__ = (
        sa.Index("computed_field_activity_table_id_status_idx", "table_id", "status"),
        sa.Index("computed_field_activity_base_id_status_idx", "base_id", "status"),
    )

    field_id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    active_task_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    processing_task_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    generation: Mapped[int] = mapped_column(
        sa.BigInteger,
        nullable=False,
        server_default=sa.text("0"),
    )
    estimated_complexity: Mapped[int] = mapped_column(
        sa.BigInteger,
        nullable=False,
        server_default=sa.text("0"),
    )
    estimated_dirty_records: Mapped[int] = mapped_column(
        sa.BigInteger,
        nullable=False,
        server_default=sa.text("0"),
    )
    has_all_target_records: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    )
    queued_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    started_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_completed_at: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    last_error: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    extensions: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: ComputedTableActivity
class ComputedTableActivity(MetaBase):
    __tablename__ = "computed_table_activity"
    __table_args__ = (sa.Index("computed_table_activity_base_id_status_idx", "base_id", "status"),)

    table_id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    calculating_field_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    queued_field_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    estimated_complexity: Mapped[int] = mapped_column(
        sa.BigInteger,
        nullable=False,
        server_default=sa.text("0"),
    )
    recent_completions: Mapped[dict] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    generation: Mapped[int] = mapped_column(
        sa.BigInteger,
        nullable=False,
        server_default=sa.text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: ComputedTaskFieldRef
class ComputedTaskFieldRef(MetaBase):
    __tablename__ = "computed_task_field_ref"
    __table_args__ = (
        sa.PrimaryKeyConstraint("task_id", "field_id", name="computed_task_field_ref_pkey"),
        sa.Index("computed_task_field_ref_field_id_idx", "field_id"),
        sa.Index("computed_task_field_ref_table_id_idx", "table_id"),
    )

    task_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    was_processing: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: View
class View(MetaBase):
    __tablename__ = "view"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["table_meta.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
            name="view_table_id_fkey",
        ),
        sa.Index("view_share_id_key", "share_id", unique=True),
        sa.Index("view_order_idx", "order"),
        sa.Index("view_table_id_deleted_time_idx", "table_id", "deleted_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sort: Mapped[str | None] = mapped_column(sa.Text)
    filter: Mapped[str | None] = mapped_column(sa.Text)
    group: Mapped[str | None] = mapped_column(sa.Text)
    options: Mapped[str | None] = mapped_column(sa.Text)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    column_meta: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_locked: Mapped[bool | None] = mapped_column(sa.Boolean)
    enable_share: Mapped[bool | None] = mapped_column(sa.Boolean)
    share_id: Mapped[str | None] = mapped_column(sa.Text)
    share_meta: Mapped[str | None] = mapped_column(sa.Text)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: Ops
class Ops(MetaBase):
    __tablename__ = "ops"
    __table_args__ = (
        sa.Index(
            "ops_collection_doc_id_version_key",
            "collection",
            "doc_id",
            "version",
            unique=True,
        ),
        sa.Index("ops_collection_created_time_idx", "collection", "created_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    collection: Mapped[str] = mapped_column(sa.Text, nullable=False)
    doc_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    operation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: Reference
class Reference(MetaBase):
    __tablename__ = "reference"
    __table_args__ = (
        sa.Index(
            "reference_to_field_id_from_field_id_key",
            "to_field_id",
            "from_field_id",
            unique=True,
        ),
        sa.Index("reference_from_field_id_idx", "from_field_id"),
        sa.Index("reference_to_field_id_idx", "to_field_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    from_field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    to_field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: User
class User(MetaBase):
    __tablename__ = "users"
    __table_args__ = (
        sa.Index("users_phone_key", "phone", unique=True),
        sa.Index("users_email_key", "email", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password: Mapped[str | None] = mapped_column(sa.Text)
    salt: Mapped[str | None] = mapped_column(sa.Text)
    phone: Mapped[str | None] = mapped_column(sa.Text)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    avatar: Mapped[str | None] = mapped_column(sa.Text)
    is_system: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_admin: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_trial_used: Mapped[bool | None] = mapped_column(sa.Boolean)
    lang: Mapped[str | None] = mapped_column(sa.Text)
    notify_meta: Mapped[str | None] = mapped_column(sa.Text)
    last_sign_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    deactivated_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    permanent_deleted_time: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
    )
    ref_meta: Mapped[str | None] = mapped_column(sa.Text)


# prisma: Account
class Account(MetaBase):
    __tablename__ = "account"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="account_user_id_fkey",
        ),
        sa.Index("account_provider_provider_id_key", "provider", "provider_id", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: Attachments
class Attachments(MetaBase):
    __tablename__ = "attachments"
    __table_args__ = (sa.Index("attachments_token_key", "token", unique=True),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    token: Mapped[str] = mapped_column(sa.Text, nullable=False)
    hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    mimetype: Mapped[str] = mapped_column(sa.Text, nullable=False)
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    width: Mapped[int | None] = mapped_column(sa.Integer)
    height: Mapped[int | None] = mapped_column(sa.Integer)
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    thumbnail_path: Mapped[str | None] = mapped_column(sa.Text)


# prisma: AttachmentsTable
class AttachmentsTable(MetaBase):
    __tablename__ = "attachments_table"
    __table_args__ = (
        sa.Index("attachments_table_table_id_record_id_idx", "table_id", "record_id"),
        sa.Index("attachments_table_table_id_field_id_idx", "table_id", "field_id"),
        sa.Index("attachments_table_attachment_id_idx", "attachment_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    attachment_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: Collaborator
class Collaborator(MetaBase):
    __tablename__ = "collaborator"
    __table_args__ = (
        sa.Index(
            "collaborator_resource_type_resource_id_principal_id_princip_key",
            "resource_type",
            "resource_id",
            "principal_id",
            "principal_type",
            unique=True,
        ),
        sa.Index("collaborator_resource_id_idx", "resource_id"),
        sa.Index("collaborator_principal_id_idx", "principal_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    role_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    principal_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    principal_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: Invitation
class Invitation(MetaBase):
    __tablename__ = "invitation"
    __table_args__ = (
        sa.Index("invitation_base_id_idx", "base_id"),
        sa.Index("invitation_space_id_idx", "space_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str | None] = mapped_column(sa.Text)
    space_id: Mapped[str | None] = mapped_column(sa.Text)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    invitation_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    expired_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    create_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: InvitationRecord
class InvitationRecord(MetaBase):
    __tablename__ = "invitation_record"
    __table_args__ = (
        sa.Index("invitation_record_invitation_id_idx", "invitation_id"),
        sa.Index("invitation_record_base_id_idx", "base_id"),
        sa.Index("invitation_record_space_id_idx", "space_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    invitation_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str | None] = mapped_column(sa.Text)
    space_id: Mapped[str | None] = mapped_column(sa.Text)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    inviter: Mapped[str] = mapped_column(sa.Text, nullable=False)
    accepter: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: Notification
class Notification(MetaBase):
    __tablename__ = "notification"
    __table_args__ = (
        sa.Index(
            "notification_to_user_id_is_read_created_time_idx",
            "to_user_id",
            "is_read",
            "created_time",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    from_user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    to_user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    message: Mapped[str] = mapped_column(sa.Text, nullable=False)
    message_i18n: Mapped[str | None] = mapped_column(sa.Text)
    severity: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'info'::text"),
    )
    url_path: Mapped[str | None] = mapped_column(sa.Text)
    is_read: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    )
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: AccessToken
class AccessToken(MetaBase):
    __tablename__ = "access_token"
    __table_args__ = (
        sa.Index("access_token_user_id_idx", "user_id"),
        sa.Index("access_token_client_id_idx", "client_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scopes: Mapped[str] = mapped_column(sa.Text, nullable=False)
    space_ids: Mapped[str | None] = mapped_column(sa.Text)
    base_ids: Mapped[str | None] = mapped_column(sa.Text)
    sign: Mapped[str] = mapped_column(sa.Text, nullable=False)
    client_id: Mapped[str | None] = mapped_column(sa.Text)
    has_full_access: Mapped[bool | None] = mapped_column(sa.Boolean)
    expired_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
    )
    last_used_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: Setting
class Setting(MetaBase):
    __tablename__ = "setting"
    __table_args__ = (sa.Index("setting_name_key", "name", unique=True),)

    # schema has no PK; the unique column doubles as ORM identity
    name: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    content: Mapped[str | None] = mapped_column(sa.Text)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: OAuthApp
class OAuthApp(MetaBase):
    __tablename__ = "oauth_app"
    __table_args__ = (sa.Index("oauth_app_client_id_key", "client_id", unique=True),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    logo: Mapped[str | None] = mapped_column(sa.Text)
    homepage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    client_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    redirect_uris: Mapped[str | None] = mapped_column(sa.Text)
    scopes: Mapped[str | None] = mapped_column(sa.Text)
    allow_device_flow: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    )
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: OAuthAppAuthorized
class OAuthAppAuthorized(MetaBase):
    __tablename__ = "oauth_app_authorized"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_app.client_id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="oauth_app_authorized_client_id_fkey",
        ),
        sa.Index("oauth_app_authorized_client_id_user_id_key", "client_id", "user_id", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    client_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    authorized_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
    )


# prisma: OAuthAppSecret
class OAuthAppSecret(MetaBase):
    __tablename__ = "oauth_app_secret"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_app.client_id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="oauth_app_secret_client_id_fkey",
        ),
        sa.Index("oauth_app_secret_secret_key", "secret", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    client_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    secret: Mapped[str] = mapped_column(sa.Text, nullable=False)
    masked_secret: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_used_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: OAuthAppToken
class OAuthAppToken(MetaBase):
    __tablename__ = "oauth_app_token"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["app_secret_id"],
            ["oauth_app_secret.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="oauth_app_token_app_secret_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_app.client_id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="oauth_app_token_client_id_fkey",
        ),
        sa.Index("oauth_app_token_refresh_token_sign_key", "refresh_token_sign", unique=True),
        sa.Index("oauth_app_token_client_id_idx", "client_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    client_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    app_secret_id: Mapped[str | None] = mapped_column(sa.Text)
    refresh_token_sign: Mapped[str] = mapped_column(sa.Text, nullable=False)
    expired_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
    )
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: RecordHistory
class RecordHistory(MetaBase):
    __tablename__ = "record_history"
    __table_args__ = (
        sa.Index(
            "record_history_table_id_record_id_created_time_idx",
            "table_id",
            "record_id",
            "created_time",
        ),
        sa.Index("record_history_table_id_created_time_idx", "table_id", "created_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    before: Mapped[str] = mapped_column(sa.Text, nullable=False)
    after: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: Trash
class Trash(MetaBase):
    __tablename__ = "trash"
    __table_args__ = (
        sa.Index(
            "trash_resource_type_resource_id_key",
            "resource_type",
            "resource_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(sa.Text)
    deleted_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    deleted_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: TableTrash
class TableTrash(MetaBase):
    __tablename__ = "table_trash"
    __table_args__ = (sa.Index("table_trash_table_id_idx", "table_id"),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# prisma: RecordTrash
class RecordTrash(MetaBase):
    __tablename__ = "record_trash"
    __table_args__ = (sa.Index("record_trash_table_id_record_id_idx", "table_id", "record_id"),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'deleted'::text"),
    )
    record_created_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    record_created_by: Mapped[str | None] = mapped_column(sa.Text)
    record_last_modified_time: Mapped[datetime | None] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
    )
    record_last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    operation_id: Mapped[str | None] = mapped_column(sa.Text)


# prisma: RecordRemovalTombstone
class RecordRemovalTombstone(MetaBase):
    __tablename__ = "record_removal_tombstone"
    __table_args__ = (
        sa.Index("record_removal_tombstone_table_id_record_id_idx", "table_id", "record_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: Plugin
class Plugin(MetaBase):
    __tablename__ = "plugin"
    __table_args__ = (sa.Index("plugin_secret_key", "secret", unique=True),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    detail_desc: Mapped[str | None] = mapped_column(sa.Text)
    logo: Mapped[str] = mapped_column(sa.Text, nullable=False)
    help_url: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    positions: Mapped[str] = mapped_column(sa.Text, nullable=False)
    url: Mapped[str | None] = mapped_column(sa.Text)
    secret: Mapped[str] = mapped_column(sa.Text, nullable=False)
    masked_secret: Mapped[str] = mapped_column(sa.Text, nullable=False)
    i18n: Mapped[str | None] = mapped_column(sa.Text)
    config: Mapped[str | None] = mapped_column(sa.Text)
    plugin_user: Mapped[str | None] = mapped_column(sa.Text)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: PluginInstall
class PluginInstall(MetaBase):
    __tablename__ = "plugin_install"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["plugin_id"],
            ["plugin.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="plugin_install_plugin_id_fkey",
        ),
        sa.Index("plugin_install_position_id_idx", "position_id"),
        sa.Index("plugin_install_base_id_idx", "base_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    plugin_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    position_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    position: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage: Mapped[str | None] = mapped_column(sa.Text)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: Dashboard
class Dashboard(MetaBase):
    __tablename__ = "dashboard"
    __table_args__ = (sa.Index("dashboard_base_id_idx", "base_id"),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    layout: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: Comment
class Comment(MetaBase):
    __tablename__ = "comment"
    __table_args__ = (sa.Index("comment_table_id_record_id_idx", "table_id", "record_id"),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    quote_Id: Mapped[str | None] = mapped_column(sa.Text)
    content: Mapped[str | None] = mapped_column(sa.Text)
    reaction: Mapped[str | None] = mapped_column(sa.Text)
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: CommentSubscription
class CommentSubscription(MetaBase):
    __tablename__ = "comment_subscription"
    __table_args__ = (
        sa.Index(
            "comment_subscription_table_id_record_id_key",
            "table_id",
            "record_id",
            unique=True,
        ),
        sa.Index("comment_subscription_table_id_record_id_idx", "table_id", "record_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: Integration
class Integration(MetaBase):
    __tablename__ = "integration"
    __table_args__ = (
        sa.Index("integration_resource_id_key", "resource_id", unique=True),
        sa.Index("integration_resource_id_idx", "resource_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    config: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enable: Mapped[bool | None] = mapped_column(sa.Boolean)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: PluginPanel
class PluginPanel(MetaBase):
    __tablename__ = "plugin_panel"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["table_meta.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="plugin_panel_table_id_fkey",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    layout: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: PluginContextMenu
class PluginContextMenu(MetaBase):
    __tablename__ = "plugin_context_menu"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["table_meta.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="plugin_context_menu_table_id_fkey",
        ),
        sa.Index("plugin_context_menu_plugin_install_id_key", "plugin_install_id", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    table_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    plugin_install_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: UserLastVisit
class UserLastVisit(MetaBase):
    __tablename__ = "user_last_visit"
    __table_args__ = (
        sa.Index(
            "user_last_visit_user_id_resource_type_resource_id_key",
            "user_id",
            "resource_type",
            "resource_id",
            unique=True,
        ),
        sa.Index(
            "user_last_visit_user_id_resource_type_parent_resource_id_idx",
            "user_id",
            "resource_type",
            "parent_resource_id",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parent_resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_visit_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: Template
class Template(MetaBase):
    __tablename__ = "template"
    __table_args__ = (sa.Index("template_base_id_key", "base_id", unique=True),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str | None] = mapped_column(sa.Text)
    cover: Mapped[str | None] = mapped_column(sa.Text)
    name: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)
    markdown_description: Mapped[str | None] = mapped_column(sa.Text)
    category_id: Mapped[list[str] | None] = mapped_column(postgresql.ARRAY(sa.Text))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    is_system: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_published: Mapped[bool | None] = mapped_column(sa.Boolean)
    featured: Mapped[bool | None] = mapped_column(sa.Boolean)
    snapshot: Mapped[str | None] = mapped_column(sa.Text)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    usage_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    publish_info: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    visit_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )


# prisma: TemplateCategory
class TemplateCategory(MetaBase):
    __tablename__ = "template_category"
    __table_args__ = (sa.Index("template_category_name_key", "name", unique=True),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)


# prisma: Task
class Task(MetaBase):
    __tablename__ = "task"
    __table_args__ = (sa.Index("task_type_status_idx", "type", "status"),)

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    snapshot: Mapped[str | None] = mapped_column(sa.Text)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: TaskRun
class TaskRun(MetaBase):
    __tablename__ = "task_run"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="task_run_task_id_fkey",
        ),
        sa.Index("task_run_task_id_status_idx", "task_id", "status"),
        sa.Index("task_run_status_base_id_created_time_idx", "status", "base_id", "created_time"),
        sa.Index("task_run_status_last_modified_time_idx", "status", "last_modified_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    task_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    depends_on_run_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=sa.text("ARRAY[]::text[]"),
    )
    spent: Mapped[int | None] = mapped_column(sa.Integer)
    log: Mapped[str | None] = mapped_column(sa.Text)
    error_msg: Mapped[str | None] = mapped_column(sa.Text)
    started_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: TaskReference
class TaskReference(MetaBase):
    __tablename__ = "task_reference"
    __table_args__ = (
        sa.Index(
            "task_reference_to_field_id_from_field_id_key",
            "to_field_id",
            "from_field_id",
            unique=True,
        ),
        sa.Index("task_reference_from_field_id_idx", "from_field_id"),
        sa.Index("task_reference_to_field_id_idx", "to_field_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    from_field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    to_field_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: Waitlist
class Waitlist(MetaBase):
    __tablename__ = "waitlist"
    __table_args__ = (sa.Index("waitlist_email_key", "email", unique=True),)

    # schema has no PK; the unique column doubles as ORM identity
    email: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    invite: Mapped[bool | None] = mapped_column(sa.Boolean)
    invite_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


# prisma: BaseNode
class BaseNode(MetaBase):
    __tablename__ = "base_node"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["base_node.id"],
            onupdate="CASCADE",
            ondelete="SET NULL",
            name="base_node_parent_id_fkey",
        ),
        sa.Index(
            "base_node_base_id_resource_type_resource_id_key",
            "base_id",
            "resource_type",
            "resource_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(sa.Text)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    order: Mapped[float] = mapped_column(sa.Double, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: BaseNodeFolder
class BaseNodeFolder(MetaBase):
    __tablename__ = "base_node_folder"
    __table_args__ = (
        sa.Index("base_node_folder_base_id_name_key", "base_id", "name", unique=True),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))
    last_modified_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: BaseShare
class BaseShare(MetaBase):
    __tablename__ = "base_share"
    __table_args__ = (
        sa.Index("base_share_share_id_key", "share_id", unique=True),
        sa.Index("base_share_base_id_node_id_key", "base_id", "node_id", unique=True),
        sa.Index("base_share_base_id_idx", "base_id"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    base_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    share_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password: Mapped[str | None] = mapped_column(sa.Text)
    node_id: Mapped[str | None] = mapped_column(sa.Text)
    allow_save: Mapped[bool | None] = mapped_column(sa.Boolean)
    allow_copy: Mapped[bool | None] = mapped_column(sa.Boolean)
    allow_edit: Mapped[bool | None] = mapped_column(sa.Boolean)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.text("true"),
    )
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_modified_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# prisma: ShortLink
class ShortLink(MetaBase):
    __tablename__ = "short_link"
    __table_args__ = (
        sa.Index("short_link_code_key", "code", unique=True),
        sa.Index("short_link_type_resource_id_key", "type", "resource_id", unique=True),
        sa.Index("short_link_deleted_time_idx", "deleted_time"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True, nullable=False)
    code: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_time: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(precision=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    deleted_time: Mapped[datetime | None] = mapped_column(postgresql.TIMESTAMP(precision=3))


# internal undo/redo journal (migration-managed, no prisma model)
class UndoLog(MetaBase):
    __tablename__ = "__undo_log"
    __table_args__ = (sa.Index("__undo_log_batch_id_idx", "batch_id"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    operation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    record_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    old_row: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    new_row: Mapped[dict | None] = mapped_column(postgresql.JSONB)
    created_at: Mapped[datetime] = mapped_column(
        postgresql.TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=sa.text("now()"),
    )
