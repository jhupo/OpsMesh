"""Replace conversation delegation rounds with one native SDK execution graph."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0122_native_agent_routing"
down_revision = "0121_text_knowledge_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM conversation_turns WHERE status NOT IN
                       ('completed', 'failed', 'cancelled'))
               OR EXISTS (SELECT 1 FROM agent_runs WHERE status NOT IN
                          ('completed', 'failed', 'cancelled')) THEN
                RAISE EXCEPTION 'Drain or cancel agent runs and conversation turns before upgrade';
            END IF;
        END $$;
        UPDATE tasks t SET input = coalesce(t.input, '{}'::jsonb) || jsonb_build_object(
            '_conversation_execution_0122', to_jsonb(e),
            '_conversation_turn_round_0122', ct.round,
            '_conversation_event_rounds_0122', coalesce((
                SELECT jsonb_agg(jsonb_build_object('id', ce.id, 'round', ce.round))
                FROM conversation_events ce WHERE ce.turn_id = ct.id
            ), '[]'::jsonb))
        FROM conversation_executions e JOIN conversation_turns ct ON ct.id = e.turn_id
        WHERE t.id = e.task_id;
        DELETE FROM conversation_executions WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY turn_id ORDER BY (purpose = 'manager') DESC,
                    round DESC, created_at DESC, id DESC
                ) AS rank FROM conversation_executions
            ) ranked WHERE rank > 1
        );
    """)
    op.drop_constraint(
        "uq_conversation_executions_turn_id", "conversation_executions", type_="unique"
    )
    for column in ("key", "purpose", "parent_run_id", "round"):
        op.drop_column("conversation_executions", column)
    op.create_unique_constraint(
        "uq_conversation_executions_turn_id", "conversation_executions", ["turn_id"]
    )
    op.drop_column("conversation_turns", "round")
    op.drop_column("conversation_events", "round")
    op.add_column("conversation_turns", sa.Column("final_agent", JSONB(), nullable=True))
    op.add_column(
        "model_usage_records",
        sa.Column("usage_key", sa.String(80), nullable=False, server_default="single_model"),
    )
    op.alter_column("model_usage_records", "usage_key", server_default=None)
    op.drop_constraint(
        "uq_model_usage_records_run_job_attempt_request", "model_usage_records", type_="unique"
    )
    op.create_unique_constraint(
        "uq_model_usage_records_run_job_attempt_request",
        "model_usage_records",
        ["workspace_id", "agent_run_id", "job_attempt", "request_sequence", "usage_key"],
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM model_usage_records GROUP BY
                       workspace_id, agent_run_id, job_attempt, request_sequence HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'Export and remove split model usage records before downgrade';
            END IF;
            IF EXISTS (SELECT 1 FROM conversation_turns WHERE status NOT IN
                       ('completed', 'failed', 'cancelled'))
               OR EXISTS (SELECT 1 FROM agent_runs WHERE status NOT IN
                          ('completed', 'failed', 'cancelled')) THEN
                RAISE EXCEPTION 'Drain or cancel agent runs and conversation turns before downgrade';
            END IF;
        END $$;
    """)
    op.drop_constraint(
        "uq_model_usage_records_run_job_attempt_request", "model_usage_records", type_="unique"
    )
    op.drop_column("model_usage_records", "usage_key")
    op.create_unique_constraint(
        "uq_model_usage_records_run_job_attempt_request",
        "model_usage_records",
        ["workspace_id", "agent_run_id", "job_attempt", "request_sequence"],
    )
    op.drop_column("conversation_turns", "final_agent")
    op.add_column(
        "conversation_turns", sa.Column("round", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "conversation_events", sa.Column("round", sa.Integer(), server_default="0", nullable=False)
    )
    op.drop_constraint(
        "uq_conversation_executions_turn_id", "conversation_executions", type_="unique"
    )
    op.add_column(
        "conversation_executions",
        sa.Column("key", sa.String(128), server_default="manager:0", nullable=False),
    )
    op.add_column(
        "conversation_executions",
        sa.Column("purpose", sa.String(16), server_default="manager", nullable=False),
    )
    op.add_column(
        "conversation_executions",
        sa.Column("round", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("conversation_executions", sa.Column("parent_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversation_executions_parent_run_id_agent_runs",
        "conversation_executions",
        "agent_runs",
        ["parent_run_id"],
        ["id"],
    )
    op.execute("""
        UPDATE conversation_executions e SET
            key = t.input->'_conversation_execution_0122'->>'key',
            purpose = t.input->'_conversation_execution_0122'->>'purpose',
            round = (t.input->'_conversation_execution_0122'->>'round')::integer,
            parent_run_id = (t.input->'_conversation_execution_0122'->>'parent_run_id')::uuid
        FROM tasks t WHERE t.id = e.task_id AND t.input ? '_conversation_execution_0122';
        INSERT INTO conversation_executions
        SELECT saved.* FROM tasks t,
            jsonb_populate_record(NULL::conversation_executions,
                                 t.input->'_conversation_execution_0122') saved
        WHERE t.input ? '_conversation_execution_0122'
          AND NOT EXISTS (SELECT 1 FROM conversation_executions e WHERE e.id = saved.id);
        UPDATE conversation_turns ct SET round = (t.input->>'_conversation_turn_round_0122')::integer
        FROM tasks t JOIN conversation_executions e ON e.task_id = t.id
        WHERE ct.id = e.turn_id AND t.input ? '_conversation_turn_round_0122';
        UPDATE conversation_events ce SET round = (saved->>'round')::integer
        FROM tasks t, jsonb_array_elements(t.input->'_conversation_event_rounds_0122') saved
        WHERE ce.id = (saved->>'id')::bigint;
    """)
    op.create_unique_constraint(
        "uq_conversation_executions_turn_id", "conversation_executions", ["turn_id", "key"]
    )
    for table in ("conversation_turns", "conversation_events", "conversation_executions"):
        op.alter_column(table, "round", server_default=None)
    for column in ("key", "purpose"):
        op.alter_column("conversation_executions", column, server_default=None)
