import asyncio
from datetime import UTC, datetime
from subprocess import TimeoutExpired
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.command_output import (
    bounded_error,
    bounded_text,
    command_failure_metadata,
    positive_int_limit,
)
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeCommandResult,
)
from backend.app.runtime_manager.events import RuntimeEventLog
from backend.app.runtime_manager.lifecycle.guards import require_container
from backend.app.runtime_manager.security_events import RuntimeSecurityEventRecorder
from backend.app.runtime_manager.models import RuntimeCommand, WorkspaceRuntime


class RuntimeCommandExecutor:
    def __init__(
        self,
        session: Session,
        docker_client: DockerRuntimeClient,
        event_log: RuntimeEventLog,
        security_events: RuntimeSecurityEventRecorder,
    ) -> None:
        self._session = session
        self._docker = docker_client
        self._events = event_log
        self._security_events = security_events

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        record = self._create_command_record(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )
        return self.execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
            input_file=input_file,
            working_dir=working_dir,
        )

    async def execute_command_async(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        record = self._create_command_record(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )
        return await self.execute_existing_command_async(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
            input_file=input_file,
            working_dir=working_dir,
        )

    def execute_existing_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        timeout_seconds = self._prepare_execution(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )
        try:
            result = self._execute_docker_command(
                runtime=runtime,
                command=command,
                timeout_seconds=timeout_seconds,
                input_file=input_file,
                working_dir=working_dir,
            )
        except Exception as exc:
            self._record_execution_error(
                runtime=runtime,
                record=record,
                command=command,
                timeout_seconds=timeout_seconds,
                error=exc,
            )
        else:
            self._record_execution_result(runtime, record, command, result)
        return self._persist_result(record)

    async def execute_existing_command_async(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommand:
        timeout_seconds = self._prepare_execution(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )
        try:
            result = await asyncio.to_thread(
                self._execute_docker_command,
                runtime=runtime,
                command=command,
                timeout_seconds=timeout_seconds,
                input_file=input_file,
                working_dir=working_dir,
            )
        except Exception as exc:
            self._record_execution_error(
                runtime=runtime,
                record=record,
                command=command,
                timeout_seconds=timeout_seconds,
                error=exc,
            )
        else:
            self._record_execution_result(runtime, record, command, result)
        return self._persist_result(record)

    def _create_command_record(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        if runtime.workspace_id != workspace_id:
            raise PermissionError("Runtime does not belong to workspace")
        require_container(runtime)
        record = RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            status="running",
            started_at=datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def _prepare_execution(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
    ) -> int:
        if runtime.workspace_id != workspace_id:
            raise PermissionError("Runtime does not belong to workspace")
        require_container(runtime)
        timeout_value = runtime.limits.get("timeout_seconds", 60)
        timeout_seconds = timeout_value if isinstance(timeout_value, int) else 60
        record.command = command
        record.status = "running"
        record.started_at = datetime.now(UTC)
        self._session.flush()
        return timeout_seconds

    def _execute_docker_command(
        self,
        *,
        runtime: WorkspaceRuntime,
        command: list[str],
        timeout_seconds: int,
        input_file: RuntimeCommandInputFile | None,
        working_dir: str | None,
    ) -> RuntimeCommandResult:
        container_id = runtime.docker_container_id or ""
        if input_file is not None and working_dir is not None:
            return self._docker.exec_command(
                container_id,
                command,
                timeout_seconds,
                input_file=input_file,
                working_dir=working_dir,
            )
        if input_file is not None:
            return self._docker.exec_command(
                container_id,
                command,
                timeout_seconds,
                input_file=input_file,
            )
        if working_dir is not None:
            return self._docker.exec_command(
                container_id,
                command,
                timeout_seconds,
                working_dir=working_dir,
            )
        return self._docker.exec_command(container_id, command, timeout_seconds)

    def _record_execution_error(
        self,
        *,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
        timeout_seconds: int,
        error: Exception,
    ) -> None:
        if isinstance(error, TimeoutExpired):
            self._fail_command(
                record,
                status="timeout",
                exit_code=None,
                stderr=f"Command exceeded timeout of {timeout_seconds} seconds",
            )
            self._events.append(
                runtime,
                "runtime.command.timeout",
                " ".join(command),
                metadata=command_failure_metadata(record, "timeout", str(error)),
            )
            return
        self._fail_command(
            record,
            status="failed",
            exit_code=None,
            stderr=bounded_error(error),
        )
        self._events.append(
            runtime,
            "runtime.command.failed",
            " ".join(command),
            metadata=command_failure_metadata(record, "docker_exec_failed", str(error)),
        )

    def _record_execution_result(
        self,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
        result: RuntimeCommandResult,
    ) -> None:
        self._complete_command(runtime, record, result)
        self._events.append(runtime, "runtime.command.completed", " ".join(command))

    def _persist_result(self, record: RuntimeCommand) -> RuntimeCommand:
        self._session.commit()
        self._session.refresh(record)
        return record

    def _complete_command(
        self,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        result: RuntimeCommandResult,
    ) -> None:
        record.exit_code = result.exit_code
        max_output_bytes = positive_int_limit(runtime.limits.get("max_output_bytes"), 256_000)
        stdout, stdout_truncated, stdout_bytes = bounded_text(result.stdout, max_output_bytes)
        stderr, stderr_truncated, stderr_bytes = bounded_text(result.stderr, max_output_bytes)
        record.stdout = stdout
        record.stderr = stderr
        record.status = "completed" if result.exit_code == 0 else "failed"
        record.completed_at = datetime.now(UTC)
        if stdout_truncated or stderr_truncated:
            metadata = {
                "command_id": str(record.id),
                "max_output_bytes": max_output_bytes,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
            self._events.append(
                runtime,
                "runtime.command.output_limited",
                "Command output exceeded runtime policy.",
                metadata=metadata,
            )
            self._security_events.record_policy_limit(
                runtime,
                action="runtime.command.output_limited",
                reason="Runtime command output exceeded policy.",
                metadata=metadata,
            )

    def _fail_command(
        self,
        record: RuntimeCommand,
        *,
        status: str,
        exit_code: int | None,
        stderr: str,
    ) -> None:
        record.exit_code = exit_code
        record.stdout = ""
        record.stderr = stderr
        record.status = status
        record.completed_at = datetime.now(UTC)
