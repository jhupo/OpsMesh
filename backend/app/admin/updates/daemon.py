from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

from filelock import FileLock
from opsmesh_operator.backups import BackupStore, digest
from opsmesh_operator.deployments import deployment_for
from opsmesh_operator.installation import Installation
from opsmesh_operator.releases import ReleaseSource
from opsmesh_operator.update_state import Journal, UpdatePlan
from packaging.version import Version
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.admin.updates.models import (
    PlatformInstallation,
    PlatformUpdateEvent,
    PlatformUpdateJob,
)
from backend.app.admin.updates.service import UpdateService
from backend.app.db.session import SessionLocal, engine
from backend.app.operations.models import WorkerLease
from backend.app.self_hosted.models import SelfHostedJobClaim, SelfHostedMcpJob

logger = logging.getLogger(__name__)


class HostUpdater:
    def __init__(self, installation: Installation) -> None:
        self.installation = installation
        self.deployment = deployment_for(installation)
        self.backups = BackupStore(installation)

    def revision(self) -> str:
        with SessionLocal() as session:
            revisions = (
                session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            )
        if len(revisions) != 1:
            raise ValueError("Installation must have exactly one database revision")
        return str(revisions[0])

    def tick(self) -> None:
        with FileLock(self.installation.root / "operator.lock", timeout=0):
            with SessionLocal() as session:
                job = session.scalar(
                    select(PlatformUpdateJob).where(PlatformUpdateJob.active_slot == 1)
                )
                if job is None or job.status in {"ready", "recovery_required"}:
                    return
                job_id, status = job.id, job.status
            try:
                if status == "planning":
                    self.plan(job_id)
                elif status == "queued":
                    self.execute(job_id)
                elif status == "running":
                    # Never blindly replay a subprocess after a crash or migration failure.
                    path = self.installation.root / "updates" / f"{job_id}.json"
                    journal = (
                        Journal.model_validate_json(path.read_bytes()) if path.exists() else None
                    )
                    if journal is not None and journal.phase in {
                        "succeeded",
                        "rolled_back",
                        "backup_restored",
                    }:
                        self.reconcile_terminal(job_id, journal)
                    elif journal is None and not self.maintenance_active():
                        self.mark(job_id, "failed", "interrupted_before_execution")
                    else:
                        self.mark(job_id, "recovery_required", "interrupted")
            except Exception as exc:
                logger.error("Update operation failed: %s", type(exc).__name__)
                self.mark(
                    job_id,
                    "recovery_required" if self.maintenance_active() else "failed",
                    type(exc).__name__,
                )

    def plan(self, job_id: UUID) -> None:
        with SessionLocal() as session:
            job = UpdateService(session).get(job_id)
            tag, action = job.tag, job.action
        previous = self.installation.current()
        target = ReleaseSource(self.installation.repository).fetch_manifest(
            tag, self.installation.root / "downloads" / tag
        )
        revision = self.revision()
        if action == "update":
            if Version(target.tag[1:]) <= Version(previous.tag[1:]):
                raise ValueError("Use an explicit rollback plan for older releases")
            if (
                revision not in target.upgrade_from_revisions
                and revision != target.database_revision
            ):
                raise ValueError("Target does not declare this source database revision")
        elif action == "rollback":
            if revision not in target.rollback_database_revisions:
                raise ValueError("Target application cannot run against the current database")
        elif target != previous:
            raise ValueError("Backup plans must target the installed release")
        self.deployment.preflight(target)
        self.deployment.stage(target)
        plan = UpdatePlan(
            action=action,
            target=target,
            previous=previous,
            database_revision=revision,
            configuration_sha256=digest(self.installation.root / ".env"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        with SessionLocal.begin() as session:
            service = UpdateService(session)
            job = service.get(job_id, lock=True)
            if job.status != "planning":
                return  # cancellation during download cannot resurrect an update
            job.plan = plan.model_dump(mode="json")
            job.plan_sha256, job.status = plan.fingerprint(), "ready"
            service.event(job, "plan_ready")

    def execute(self, job_id: UUID) -> None:
        with SessionLocal.begin() as session:
            job = UpdateService(session).get(job_id, lock=True)
            if job.status != "queued" or not job.approved_at:
                raise ValueError("Update has not been approved")
            plan = UpdatePlan.model_validate(job.plan)
            if plan.fingerprint() != job.plan_sha256 or datetime.now(UTC) >= plan.expires_at:
                raise ValueError("Approved plan is invalid or expired")
            job.status = "running"
        if (
            self.installation.current() != plan.previous
            or self.revision() != plan.database_revision
            or digest(self.installation.root / ".env") != plan.configuration_sha256
        ):
            self.mark(job_id, "failed", "stale_plan")
            return
        root = self.installation.root
        journal = Journal(job_id=str(job_id), plan=plan, phase="preflight")
        journal.save(root)
        self.deployment.preflight(plan.target)
        self.maintenance(True)
        journal = self.checkpoint(job_id, journal, "draining")
        try:
            self.drain()
        except TimeoutError:
            self.maintenance(False)
            self.mark(job_id, "failed", "drain_timeout")
            return
        journal = self.checkpoint(job_id, journal, "stopping")
        self.deployment.stop()
        journal = self.checkpoint(job_id, journal, "backup")
        backup_id = self.backups.create(database_revision=self.revision())
        journal = self.checkpoint(job_id, journal, "backed_up", backup_id=backup_id)
        self.finish_deployment(job_id, journal)

    def finish_deployment(self, job_id: UUID, journal: Journal) -> None:
        plan = journal.plan
        if plan.action == "update":
            journal = self.checkpoint(job_id, journal, "migrating")
            if self.revision() != plan.target.database_revision:
                self.deployment.migrate(plan.target)
            if self.revision() != plan.target.database_revision:
                raise ValueError("Database migration did not reach the declared revision")
        if plan.action != "backup":
            journal = self.checkpoint(job_id, journal, "switching")
            self.deployment.switch(plan.target)
        journal = self.checkpoint(job_id, journal, "starting")
        self.deployment.start(plan.target)
        journal = self.checkpoint(job_id, journal, "health_check")
        self.deployment.healthy()
        self.checkpoint(job_id, journal, "succeeded")
        self.complete(job_id, plan)

    def reconcile_terminal(self, job_id: UUID, journal: Journal) -> None:
        """Finalize a durable outcome without repeating deployment or database restoration."""
        if journal.job_id != job_id:
            raise ValueError("Host journal job identity mismatch")
        with SessionLocal.begin() as session:
            service = UpdateService(session)
            job = service.get(job_id, lock=True)
            if journal.plan.fingerprint() != job.plan_sha256:
                raise ValueError("Host journal does not match approved plan")
            installation = session.get(PlatformInstallation, 1, with_for_update=True)
            if installation is None:
                raise ValueError("Missing installation state")
            for event in journal.history:
                if session.get(PlatformUpdateEvent, event.id) is None:
                    service.event(
                        job,
                        event.phase,
                        "host_journal_recovery",
                        event_id=event.id,
                        created_at=event.created_at,
                    )
            succeeded = journal.phase == "succeeded"
            manifest = journal.plan.target if succeeded else journal.plan.previous
            installation.maintenance = False
            installation.release_manifest = manifest.model_dump(mode="json")
            job.status, job.active_slot = "succeeded" if succeeded else "failed", None
            job.backup_id = journal.backup_id
            job.error_code = None if succeeded else journal.phase
            job.phase = journal.phase

    def checkpoint(
        self,
        job_id: UUID,
        journal: Journal,
        phase: str,
        *,
        backup_id: str | None = None,
    ) -> Journal:
        updated = journal.advance(self.installation.root, phase, backup_id=backup_id)
        with SessionLocal.begin() as session:
            service = UpdateService(session)
            job = service.get(job_id, lock=True)
            job.backup_id = updated.backup_id
            event = updated.history[-1]
            service.event(job, phase, event_id=event.id, created_at=event.created_at)
        return updated

    def complete(self, job_id: UUID, plan: UpdatePlan) -> None:
        with SessionLocal.begin() as session:
            installation = session.get(PlatformInstallation, 1, with_for_update=True)
            if installation is None:
                raise ValueError("Missing installation state")
            installation.maintenance = False
            installation.release_manifest = plan.target.model_dump(mode="json")
            service = UpdateService(session)
            job = service.get(job_id, lock=True)
            job.status, job.active_slot, job.error_code = "succeeded", None, None
            service.event(job, "succeeded")

    def maintenance(self, enabled: bool) -> None:
        with SessionLocal.begin() as session:
            installation = session.get(PlatformInstallation, 1, with_for_update=True)
            if installation is None:
                raise ValueError("Missing installation state")
            installation.maintenance = enabled

    def maintenance_active(self) -> bool:
        with SessionLocal() as session:
            installation = session.get(PlatformInstallation, 1)
            if installation is None:
                raise ValueError("Missing installation state")
            return installation.maintenance

    def drain(self) -> None:
        deadline = time.monotonic() + self.installation.timeout_seconds
        while time.monotonic() < deadline:
            with SessionLocal() as session:
                active = sum(
                    int(
                        session.scalar(
                            select(func.count()).select_from(model).where(model.status == status)
                        )
                        or 0
                    )
                    for model, status in (
                        (WorkerLease, "running"),
                        (SelfHostedJobClaim, "claimed"),
                        (SelfHostedMcpJob, "claimed"),
                    )
                )
            if not active:
                return
            time.sleep(2)
        raise TimeoutError("Active jobs did not drain; no task was forcibly replayed")

    def mark(self, job_id: UUID, status: str, code: str) -> None:
        with SessionLocal.begin() as session:
            service = UpdateService(session)
            job = service.get(job_id, lock=True)
            if job.status == "cancelled":
                return
            job.status, job.error_code = status, code
            if status == "failed":
                job.active_slot = None
            service.event(job, status)

    def recover(self, job_id: UUID, strategy: str, *, acknowledge_data_loss: bool = False) -> None:
        if strategy not in {"resume", "rollback", "restore"}:
            raise ValueError("Invalid recovery strategy")
        if strategy == "restore" and not acknowledge_data_loss:
            raise ValueError("Database restoration requires explicit data-loss acknowledgement")
        with FileLock(self.installation.root / "operator.lock", timeout=0):
            journal = Journal.model_validate_json(
                (self.installation.root / "updates" / f"{job_id}.json").read_bytes()
            )
            if journal.job_id != job_id:
                raise ValueError("Host journal job identity mismatch")
            database_available = True
            try:
                with SessionLocal() as session:
                    job = UpdateService(session).get(job_id)
                    if job.status not in {"running", "recovery_required"} or job.active_slot != 1:
                        raise ValueError("Job does not own an interrupted installation")
                    if journal.plan.fingerprint() != job.plan_sha256:
                        raise ValueError("Host journal does not match approved plan")
            except SQLAlchemyError:
                if strategy != "restore" or not acknowledge_data_loss or not journal.backup_id:
                    raise
                # Explicit root recovery can use its protected journal while the application DB
                # is unavailable. The verified backup proves admission had drained before stop.
                database_available = False
            if journal.phase in {"succeeded", "rolled_back", "backup_restored"}:
                self.reconcile_terminal(job_id, journal)
                return
            if database_available:
                revision = self.revision()
                if (
                    strategy == "rollback"
                    and revision not in journal.plan.previous.rollback_database_revisions
                ):
                    raise ValueError(
                        "Previous app cannot run on this schema; restore requires consent"
                    )
                if strategy == "resume" and revision not in {
                    journal.plan.database_revision,
                    journal.plan.target.database_revision,
                }:
                    raise ValueError("Unknown database state; refusing migration replay")
            if strategy == "restore" and not journal.backup_id:
                raise ValueError("No verified backup checkpoint to restore")
            if journal.backup_id:
                self.backups.verify(journal.backup_id)
            if database_available:
                self.maintenance(True)
                self.drain()
            self.deployment.stop()
            if not journal.backup_id:
                if strategy != "resume" or self.revision() != journal.plan.database_revision:
                    raise ValueError("No backup checkpoint; only pre-migration resume is safe")
                backup_id = self.backups.create(database_revision=self.revision())
                journal = self.checkpoint(job_id, journal, "backed_up", backup_id=backup_id)
            assert journal.backup_id is not None
            if strategy == "restore":
                self.backups.restore(journal.backup_id, acknowledge_data_loss=acknowledge_data_loss)
                engine.dispose()
                with SessionLocal.begin() as session:
                    service = UpdateService(session)
                    restored_job = service.get(job_id, lock=True)
                    restored_job.backup_id = journal.backup_id
                    for event in journal.history:
                        if session.get(PlatformUpdateEvent, event.id) is None:
                            service.event(
                                restored_job,
                                event.phase,
                                "host_journal_recovery",
                                event_id=event.id,
                                created_at=event.created_at,
                            )
                self.deployment.switch(journal.plan.previous)
                self.deployment.start(journal.plan.previous)
                self.deployment.healthy()
                journal = journal.advance(self.installation.root, "backup_restored")
                self.reconcile_terminal(job_id, journal)
            elif strategy == "rollback":
                if self.revision() not in journal.plan.previous.rollback_database_revisions:
                    raise ValueError(
                        "Previous app cannot run on this schema; restore requires consent"
                    )
                self.deployment.switch(journal.plan.previous)
                self.deployment.start(journal.plan.previous)
                self.deployment.healthy()
                journal = journal.advance(self.installation.root, "rolled_back")
                self.reconcile_terminal(job_id, journal)
            else:
                if self.revision() not in {
                    journal.plan.database_revision,
                    journal.plan.target.database_revision,
                }:
                    raise ValueError("Unknown database state; refusing migration replay")
                self.finish_deployment(job_id, journal)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--recover", type=UUID)
    parser.add_argument("--strategy", choices=["resume", "rollback", "restore"], default="resume")
    parser.add_argument("--ack-data-loss", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    updater = HostUpdater(Installation.load(args.root))
    if args.recover:
        if args.strategy == "restore" and not args.ack_data_loss:
            parser.error("restore requires --ack-data-loss")
        updater.recover(args.recover, args.strategy, acknowledge_data_loss=args.ack_data_loss)
        return
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            updater.tick()
        except Exception as exc:
            logger.error("Host updater unavailable: %s", type(exc).__name__)
        if args.once:
            return
        stopped.wait(5)


if __name__ == "__main__":
    main()
