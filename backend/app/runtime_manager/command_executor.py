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
from backend.app.runtime_manager.runtime_guards import require_container
from backend.app.runtime_manager.security_events import RuntimeSecurityEventRecorder
from backend.app.runtimes.models import RuntimeCommand, WorkspaceRuntime


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
        return self.execute_existing_command(
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
        if runtime.workspace_id != workspace_id:
            raise PermissionError("Runtime does not belong to workspace")
        require_container(runtime)
        timeout_value = runtime.limits.get("timeout_seconds", 60)
        timeout_seconds = timeout_value if isinstance(timeout_value, int) else 60
        record.command = command
        record.status = "running"
        record.started_at = datetime.now(UTC)
        self._session.flush()

        try:
            arguments: dict[str, object] = {}
            if input_file is not None:
                arguments["input_file"] = input_file
            if working_dir is not None:
                arguments["working_dir"] = working_dir
            result = self._docker.exec_command(
                runtime.docker_container_id or "",
                command,
                timeout_seconds,
                **arguments,
            )
        except TimeoutExpired as exc:
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
                metadata=command_failure_metadata(record, "timeout", str(exc)),
            )
        except Exception as exc:
            self._fail_command(
                record,
                status="failed",
                exit_code=None,
                stderr=bounded_error(exc),
            )
            self._events.append(
                runtime,
                "runtime.command.failed",
                " ".join(command),
                metadata=command_failure_metadata(record, "docker_exec_failed", str(exc)),
            )
        else:
            self._complete_command(runtime, record, result)
            self._events.append(runtime, "runtime.command.completed", " ".join(command))
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
