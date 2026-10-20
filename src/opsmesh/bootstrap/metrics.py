"""Compose low-cardinality owner facts for the private operations listener."""

from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from opsmesh.extensions.models import Invocation, ManagedDeployment
from opsmesh.identity.quotas.models import (
    StorageReservation,
    TokenReservation,
    UserCapacityAccount,
    UserQuotaLimit,
    UserStorageAccount,
    UserTokenAccount,
)
from opsmesh.orchestration.runs.models import FlowRun, Run
from opsmesh.orchestration.runtime.models import Environment
from opsmesh.telemetry.metrics import GaugeMetric
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry


def product_metrics(sessions: sessionmaker[Session]) -> list[GaugeMetric]:
    """Read facts only; never reserve, mutate or reinterpret business decisions."""
    now = datetime.now(UTC)
    result: list[GaugeMetric] = []
    try:
        with sessions() as session:
            for metric, column in (
                ("opsmesh_execution_runs", Run.status),
                ("opsmesh_sandbox_environments", Environment.status),
                ("opsmesh_flow_runs", FlowRun.status),
                ("opsmesh_managed_deployments", ManagedDeployment.status),
                ("opsmesh_extension_invocations", Invocation.status),
            ):
                result.extend(count_states(session, metric, column))
            pending = TemporalOutboxEntry.published_at.is_(None)
            total, oldest, errors = session.execute(
                select(
                    func.count(),
                    func.min(TemporalOutboxEntry.created_at),
                    func.count().filter(TemporalOutboxEntry.last_error_code.is_not(None)),
                ).where(pending)
            ).one()
            published = session.scalar(
                select(func.count()).select_from(TemporalOutboxEntry).where(~pending)
            )
            result.extend(
                [
                    GaugeMetric("opsmesh_temporal_dispatches", total, {"state": "pending"}),
                    GaugeMetric(
                        "opsmesh_temporal_dispatches", published or 0, {"state": "published"}
                    ),
                    GaugeMetric(
                        "opsmesh_temporal_oldest_pending_dispatch_age_seconds",
                        max(0, (now - oldest).total_seconds()) if oldest else 0,
                    ),
                    GaugeMetric("opsmesh_temporal_dispatch_publication_errors", errors),
                ]
            )
            ratios = session.execute(
                select(
                    UserCapacityAccount.kind,
                    func.max(
                        case(
                            (
                                UserCapacityAccount.limit_value > 0,
                                UserCapacityAccount.reserved_value
                                * 1.0
                                / UserCapacityAccount.limit_value,
                            ),
                            else_=0,
                        )
                    ),
                ).group_by(UserCapacityAccount.kind)
            )
            result.extend(
                GaugeMetric("opsmesh_user_capacity_max_usage_ratio", float(ratio), {"kind": kind})
                for kind, ratio in ratios
            )
            result.extend(
                count_states(session, "opsmesh_token_reservations", TokenReservation.status)
            )
            result.extend(
                count_states(session, "opsmesh_storage_reservations", StorageReservation.status)
            )
            for kind, used, reserved, limit in (
                (
                    "token",
                    UserTokenAccount.used_tokens,
                    UserTokenAccount.reserved_tokens,
                    UserQuotaLimit.token_limit,
                ),
                (
                    "storage",
                    UserStorageAccount.used_bytes,
                    UserStorageAccount.reserved_bytes,
                    UserQuotaLimit.storage_limit_bytes,
                ),
            ):
                ratio = session.scalar(
                    select(func.max(case((limit > 0, (used + reserved) * 1.0 / limit), else_=0)))
                    .select_from(used.class_)
                    .join(UserQuotaLimit, used.class_.user_id == UserQuotaLimit.user_id)
                )
                result.append(
                    GaugeMetric(
                        "opsmesh_user_quota_max_usage_ratio", float(ratio or 0), {"kind": kind}
                    )
                )
    except SQLAlchemyError:
        return [GaugeMetric("opsmesh_metrics_collection_success", 0, {"source": "postgres"})]
    result.append(GaugeMetric("opsmesh_metrics_collection_success", 1, {"source": "postgres"}))
    return result


def count_states(
    session: Session, metric: str, column: InstrumentedAttribute[str]
) -> list[GaugeMetric]:
    return [
        GaugeMetric(metric, count, {"state": state})
        for state, count in session.execute(select(column, func.count()).group_by(column))
    ]
