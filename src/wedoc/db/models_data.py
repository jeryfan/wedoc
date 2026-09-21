"""SQLAlchemy 2.0 models for the wedoc data-plane schema.

Table/column names, nullability, server defaults, primary keys,
unique indexes and secondary indexes mirror the upstream db-data-prisma
prisma schema and the migrated PostgreSQL database exactly.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DataBase(DeclarativeBase):
    pass


# prisma: ComputedUpdateOutbox
class ComputedUpdateOutbox(DataBase):
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
class ComputedUpdateOutboxSeed(DataBase):
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
class ComputedUpdateStageLedger(DataBase):
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
class ComputedUpdateDeadLetter(DataBase):
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
class ComputedUpdateRunHistory(DataBase):
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
class ComputedUpdatePauseScope(DataBase):
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
    updated_at: Mapped[datetime] = mapped_column(postgresql.TIMESTAMP(precision=3), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(sa.Text)


# prisma: ComputedFieldActivity
class ComputedFieldActivity(DataBase):
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
class ComputedTableActivity(DataBase):
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
class ComputedTaskFieldRef(DataBase):
    __tablename__ = "computed_task_field_ref"
    __table_args__ = (
        sa.PrimaryKeyConstraint("task_id", "field_id", name="computed_task_field_ref_pkey"),
        sa.Index("computed_task_field_ref_field_id_idx", "field_id"),
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


# prisma: RecordHistory
class RecordHistory(DataBase):
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


# prisma: TableTrash
class TableTrash(DataBase):
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
class RecordTrash(DataBase):
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
class RecordRemovalTombstone(DataBase):
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


# prisma: Attachments
class Attachments(DataBase):
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
class AttachmentsTable(DataBase):
    __tablename__ = "attachments_table"
    __table_args__ = (
        sa.Index("attachments_table_table_id_record_id_idx", "table_id", "record_id"),
        sa.Index("attachments_table_table_id_field_id_idx", "table_id", "field_id"),
        sa.Index("attachments_table_attachment_id_idx", "attachment_id"),
        sa.Index("attachments_table_token_idx", "token"),
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
