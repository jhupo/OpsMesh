"""Recover durable deployment intent and schedule bounded MCP health refreshes."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp.managed_runtime import deployment_runtime
from backend.app.capabilities.mcp.managed_service import deployment_job
from backend.app.capabilities.mcp.models import McpDeployment
from backend.app.runtime.instances.allocations import RuntimeAllocationStore
from backend.app.runtime.queues.service import RedisQueue


def reconcile_managed_mcp(session: Session, queue: RedisQueue) -> None:
    now = datetime.now(UTC)
    rows = session.scalars(
        select(McpDeployment)
        .where(
            or_(
                McpDeployment.status.in_(["queued", "waiting_capacity"]),
                (McpDeployment.status == "starting")
                & (McpDeployment.checked_at < now - timedelta(minutes=5)),
                (McpDeployment.status == "running")
                & (McpDeployment.checked_at < now - timedelta(seconds=60)),
                (McpDeployment.status == "failed")
                & (McpDeployment.last_error == "mcp_process_cleanup_failed")
                & (McpDeployment.checked_at < now - timedelta(seconds=60)),
            )
        )
        .order_by(McpDeployment.updated_at)
        .limit(50)
        .with_for_update(skip_locked=True)
    ).all()
    jobs = []
    for row in rows:
        if row.status == "waiting_capacity":
            host = deployment_runtime(session, row)
            if host is None or not RuntimeAllocationStore(session).available(host):
                continue
        if row.status == "running":
            row.action = "refresh"
            row.generation += 1
        elif row.status == "failed":
            row.action = "stop"
            row.generation += 1
        row.status = "queued"
        jobs.append(deployment_job(row))
    session.commit()
    for job in jobs:
        queue.ensure_enqueued(job)
