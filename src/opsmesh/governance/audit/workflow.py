"""Durable batched verification of the audit chain.

The Workflow only orchestrates deterministic state and waits: every database
read or write happens in an Activity, and the payloads it exchanges are the
frozen contracts from this module's ``contracts``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    # Activity entry descriptions and their frozen payloads are passed through
    # instead of being reloaded into every Workflow sandbox (Temporal sandbox
    # best practice for separate deterministic modules).
    from opsmesh.governance.audit.activities import AuditActivities
    from opsmesh.governance.audit.contracts import (
        AUDIT_VERIFICATION_WORKFLOW,
        VERIFICATION_BATCH_SIZE,
        AuditSegmentResult,
        AuditVerificationCompletion,
        AuditVerificationExecution,
        AuditVerificationOutcome,
        AuditVerificationProgress,
        VerifyAuditSegment,
    )

SEGMENT_START_TO_CLOSE_TIMEOUT = timedelta(seconds=120)
RECORD_START_TO_CLOSE_TIMEOUT = timedelta(seconds=30)
VERIFICATION_RETRY_POLICY = RetryPolicy(maximum_attempts=5)


@workflow.defn(name=AUDIT_VERIFICATION_WORKFLOW)
class AuditVerificationWorkflow:
    """Recompute one chain range in durable batches until it breaks or ends."""

    @workflow.run
    async def run(self, command: AuditVerificationExecution) -> AuditVerificationOutcome:
        checked_count = 0
        current_sequence = command.from_sequence
        try:
            while current_sequence <= command.to_sequence:
                segment_end = min(
                    current_sequence + VERIFICATION_BATCH_SIZE - 1, command.to_sequence
                )
                segment: AuditSegmentResult = await workflow.execute_activity_method(
                    AuditActivities.verify_audit_segment,
                    VerifyAuditSegment(
                        verification_id=command.verification_id,
                        from_sequence=current_sequence,
                        to_sequence=segment_end,
                    ),
                    start_to_close_timeout=SEGMENT_START_TO_CLOSE_TIMEOUT,
                    retry_policy=VERIFICATION_RETRY_POLICY,
                )
                checked_count += segment.checked_count
                await workflow.execute_activity_method(
                    AuditActivities.record_audit_verification_progress,
                    AuditVerificationProgress(
                        verification_id=command.verification_id,
                        checked_count=checked_count,
                    ),
                    start_to_close_timeout=RECORD_START_TO_CLOSE_TIMEOUT,
                    retry_policy=VERIFICATION_RETRY_POLICY,
                )
                if segment.first_broken_sequence is not None:
                    return await self._complete(
                        command,
                        status="failed",
                        first_broken_sequence=segment.first_broken_sequence,
                        failure_reason=(
                            "The audit chain breaks at sequence "
                            f"{segment.first_broken_sequence}"
                        ),
                    )
                current_sequence = segment_end + 1
            return await self._complete(command, status="succeeded")
        except asyncio.CancelledError:
            # A cancelled verification keeps its durable result honest instead
            # of staying "running" forever; the read-only chain can be
            # re-verified later with a fresh command.
            await self._complete(command, status="cancelled")
            raise

    async def _complete(
        self,
        command: AuditVerificationExecution,
        *,
        status: Literal["succeeded", "failed", "cancelled"],
        first_broken_sequence: int | None = None,
        failure_reason: str | None = None,
    ) -> AuditVerificationOutcome:
        await workflow.execute_activity_method(
            AuditActivities.complete_audit_verification,
            AuditVerificationCompletion(
                verification_id=command.verification_id,
                status=status,
                first_broken_sequence=first_broken_sequence,
                failure_reason=failure_reason,
            ),
            start_to_close_timeout=RECORD_START_TO_CLOSE_TIMEOUT,
            retry_policy=VERIFICATION_RETRY_POLICY,
        )
        return AuditVerificationOutcome(
            verification_id=command.verification_id,
            status=status,
            first_broken_sequence=first_broken_sequence,
        )
