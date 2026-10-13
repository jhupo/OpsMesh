"""Approval migration preserves historical instants in a non-UTC database."""

from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.identity.users.models import User
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema


def test_approval_timestamp_upgrade_and_downgrade_preserve_existing_decision() -> None:
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0132_webhook_execution_reference")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Migration owner")
            workspace = Workspace(owner=user, name="Migration", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            workspace_id = workspace.id
        approval_id = uuid4()
        created = datetime(2026, 9, 10, 1, 2, 3, tzinfo=UTC)
        decided = datetime(2026, 9, 10, 1, 7, 3, tzinfo=UTC)
        # Use the previous database's session timezone at both write and migration
        # boundaries. The database fixture intentionally runs outside UTC.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO approvals (id, workspace_id, approval_type, risk_level, "
                    "payload, status, created_at, decided_at) VALUES "
                    "(:id, :workspace, 'product.tool', 'high', '{}', 'approved', "
                    ":created, :decided)"
                ),
                {
                    "id": approval_id,
                    "workspace": workspace_id,
                    "created": created,
                    "decided": decided,
                },
            )
            before = connection.execute(
                text("SELECT created_at, decided_at FROM approvals WHERE id = :id"),
                {"id": approval_id},
            ).one()
            assert before.created_at.tzinfo is None
        command.upgrade(config, "head")
        with Session(engine) as session:
            approval = session.scalar(select(Approval).where(Approval.id == approval_id))
            assert approval is not None
            assert approval.created_at == created and approval.decided_at == decided
        command.downgrade(config, "0132_webhook_execution_reference")
        with engine.connect() as connection:
            after = connection.execute(
                text("SELECT created_at, decided_at FROM approvals WHERE id = :id"),
                {"id": approval_id},
            ).one()
            assert after == before
