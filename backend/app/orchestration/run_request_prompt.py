import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.tools import PRODUCT_TOOL_NAMES
from backend.app.planning.agent_plan import is_agent_planning_step
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import TeamRuntimeService

from .context_budget import ContextFragment, ContextPriority
from .task_step_review import is_pm_summary_step


@dataclass(slots=True)
class RunRequestPromptRenderer:
    session: Session

    def input_text_for_run(
        self,
        run: AgentRun,
        *,
        allowed_tools: tuple[str, ...] = (),
        runtime_metadata: dict[str, object] | None = None,
    ) -> str:
        fragments = self.context_fragments_for_run(
            run,
            allowed_tools=allowed_tools,
            runtime_metadata=runtime_metadata,
        )
        return "\n\n".join(fragment.text for fragment in fragments if fragment.text).strip()

    def context_fragments_for_run(
        self,
        run: AgentRun,
        *,
        allowed_tools: tuple[str, ...] = (),
        runtime_metadata: dict[str, object] | None = None,
    ) -> tuple[ContextFragment, ...]:
        task = (
            self.session.scalar(
                select(Task).where(
                    Task.workspace_id == run.workspace_id,
                    Task.id == run.task_id,
                )
            )
            if run.task_id is not None
            else None
        )
        if task is None and run.task_id is not None:
            raise ValueError("Run task is unavailable in workspace")
        if task is None:
            return (
                ContextFragment(
                    key="run.input",
                    text=str(run.input),
                    priority=ContextPriority.CRITICAL,
                    required=True,
                ),
            )

        fragments = [
            ContextFragment(
                key="task.objective",
                text="\n".join(part for part in (task.title, task.description) if part),
                priority=ContextPriority.CRITICAL,
                required=True,
            )
        ]
        team_context_text = self.team_context_text_for_run(
            run,
            task,
            allowed_tools=allowed_tools,
        )
        if team_context_text:
            fragments.append(
                ContextFragment(
                    key="team.summary",
                    text=team_context_text,
                    priority=ContextPriority.HIGH,
                )
            )
        if run.task_step_id is not None:
            step = self.session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.task_id == task.id,
                    TaskStep.id == run.task_step_id,
                )
            )
            if step is None:
                raise ValueError("Run step is unavailable in task")
            if step is not None:
                fragments.append(
                    ContextFragment(
                        key="step.objective",
                        text=f"Current step: {step.title}\n{step.description}".strip(),
                        priority=ContextPriority.CRITICAL,
                        required=True,
                    )
                )
                step_context_text = step_context_text_for_request(step)
                if step_context_text:
                    fragments.append(
                        ContextFragment(
                            key="step.requirements",
                            text=step_context_text,
                            priority=ContextPriority.HIGH,
                        )
                    )
                if is_pm_summary_step(step):
                    fragments.append(
                        ContextFragment(
                            key="step.output_contract",
                            text=(
                                "PM acceptance output: return JSON with decision "
                                "`approved`, `request_revision`, or `add_missing_work`; include "
                                "`summary`, optional `reasons`, `revision_requests`, and "
                                "`missing_work_packages`."
                            ),
                            priority=ContextPriority.CRITICAL,
                            required=True,
                            allow_truncation=False,
                        )
                    )
                if is_agent_planning_step(step):
                    fragments.append(
                        ContextFragment(
                            key="planning.inputs",
                            text="Planning input (untrusted task data and team roster):\n"
                            + json.dumps(
                                redact_sensitive_payload(
                                    {"input": task.input, "roster": task.team_snapshot}
                                ),
                                ensure_ascii=False,
                                default=str,
                            ),
                            priority=ContextPriority.CRITICAL,
                            required=True,
                            allow_truncation=False,
                        )
                    )
                previous_summaries = self.completed_step_summaries(
                    run.workspace_id,
                    task.id,
                    before=step.order_index,
                )
                if previous_summaries:
                    fragments.append(
                        ContextFragment(
                            key="task.completed_steps",
                            text="Completed step summaries:\n" + "\n".join(previous_summaries),
                            priority=ContextPriority.NORMAL,
                        )
                    )
        rendered_runtime_context = runtime_context_text(
            allowed_tools=allowed_tools,
            metadata=runtime_metadata or {},
        )
        if rendered_runtime_context:
            fragments.append(
                ContextFragment(
                    key="runtime.capabilities",
                    text=rendered_runtime_context,
                    priority=ContextPriority.HIGH,
                )
            )
        return tuple(fragments)

    def team_context_text_for_run(
        self,
        run: AgentRun,
        task: Task,
        *,
        allowed_tools: tuple[str, ...] = (),
    ) -> str:
        if task.agent_team_id is None:
            return ""
        team = self.session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == run.workspace_id,
                AgentTeam.id == task.agent_team_id,
            )
        )
        if team is None:
            return ""
        runtime_state = TeamRuntimeService(self.session).get_state(
            workspace_id=run.workspace_id,
            team_id=team.id,
            initialize=True,
        )
        runtime_status = (
            f"{runtime_state.status}/{runtime_state.runtime_status}"
            if runtime_state is not None
            else "unknown"
        )
        lines = [
            "Team context:",
            f"- Team: {team.name} ({team.team_type})",
            f"- Runtime: {runtime_status}",
        ]
        mailbox_tools = mailbox_tool_names(allowed_tools)
        if mailbox_tools:
            lines.append(
                "- Use mailbox tools to read handoffs and coordinate with teammates: "
                + ", ".join(mailbox_tools)
            )
        else:
            lines.append("- Mailbox tools are not attached for this run.")
        return "\n".join(lines)

    def completed_step_summaries(
        self, workspace_id: UUID, task_id: UUID, *, before: int
    ) -> list[str]:
        completed_steps = self.session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == "completed",
                TaskStep.order_index < before,
                TaskStep.result_summary.is_not(None),
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            f"- {step.title}: {step.result_summary}"
            for step in completed_steps
            if step.result_summary
        ]


def step_context_text_for_request(step: TaskStep) -> str:
    lines: list[str] = []
    if step.work_package_id:
        lines.append(f"- Work package: {redact_sensitive_text(step.work_package_id)}")
    if step.required_role:
        lines.append(f"- Required role: {redact_sensitive_text(step.required_role)}")
    required_skills = runtime_text_list(step.required_skills)
    if required_skills:
        lines.append("- Required skills: " + ", ".join(required_skills))
    expected_artifacts = runtime_text_list(step.expected_artifacts)
    if expected_artifacts:
        lines.append("- Expected artifacts: " + "; ".join(expected_artifacts))
    acceptance_criteria = runtime_text_list(step.acceptance_criteria)
    if acceptance_criteria:
        lines.append("- Acceptance criteria: " + "; ".join(acceptance_criteria))
    review_policy = redact_sensitive_payload(step.review_policy)
    if isinstance(review_policy, dict) and review_policy:
        lines.append(
            "- Review policy keys: " + ", ".join(sorted(str(key) for key in review_policy))
        )
    return "Current step requirements:\n" + "\n".join(lines) if lines else ""


def runtime_context_text(
    *,
    allowed_tools: tuple[str, ...],
    metadata: dict[str, object],
) -> str:
    lines: list[str] = ["Runtime capabilities and evidence:"]
    if allowed_tools:
        lines.append("- Available tools: " + ", ".join(sorted(allowed_tools)))
        product_tools = sorted(tool for tool in allowed_tools if tool in PRODUCT_TOOL_NAMES)
        mcp_tools = sorted(tool for tool in allowed_tools if tool not in PRODUCT_TOOL_NAMES)
        if product_tools:
            lines.append("- Product tools: " + ", ".join(product_tools))
        if mcp_tools:
            lines.append("- MCP tools: " + ", ".join(mcp_tools))
    else:
        lines.append("- Available tools: none attached to this run.")

    mailbox = metadata.get("agent_mailbox")
    if isinstance(mailbox, dict):
        unread_count = mailbox.get("unread_count", 0)
        pending_count = mailbox.get("pending_count", 0)
        lines.append(f"- Mailbox: {unread_count} unread, {pending_count} pending.")
        latest_messages = mailbox.get("latest_unread_messages")
        if isinstance(latest_messages, list) and latest_messages:
            lines.append("- Latest unread mailbox messages:")
            for message in latest_messages[:5]:
                rendered = runtime_mailbox_message_line(message)
                if rendered:
                    lines.append(f"  - {rendered}")
        elif mailbox_tool_names(allowed_tools):
            lines.append("- No unread mailbox messages were found in this run scope.")

    if mailbox_tool_names(allowed_tools):
        lines.append(
            "- For complete handoff threads, call get_agent_inbox or "
            "list_agent_thread_messages before claiming missing team context."
        )
    project_workspace = metadata.get("project_workspace")
    if isinstance(project_workspace, dict):
        lines.extend(project_workspace_lines(project_workspace))
    lines.append(
        "- Treat this section as platform evidence. Do not claim missing platform "
        "context unless it is absent here and unavailable through listed tools."
    )
    return "\n".join(lines)


def project_workspace_lines(value: dict[str, object]) -> list[str]:
    lines = ["- Project workspace:"]
    root_path = value.get("root_path")
    if isinstance(root_path, str):
        lines.append(f"  - Root: {redact_sensitive_text(root_path)}")
    project = value.get("project")
    if isinstance(project, dict):
        lines.append(
            "  - Paths: input={input_path}, work={work_path}, output={output_path}".format(
                input_path=redact_sensitive_text(str(project.get("input_path") or "")),
                work_path=redact_sensitive_text(str(project.get("work_path") or "")),
                output_path=redact_sensitive_text(str(project.get("output_path") or "")),
            )
        )
    configuration = value.get("configuration")
    if isinstance(configuration, dict):
        safe_configuration = redact_sensitive_payload(configuration)
        lines.append(
            "  - Configuration data: "
            + json.dumps(safe_configuration, sort_keys=True, ensure_ascii=False)
        )
    files = value.get("files")
    if isinstance(files, list):
        paths = [
            str(item.get("project_path"))
            for item in files
            if isinstance(item, dict) and isinstance(item.get("project_path"), str)
        ]
        lines.append("  - Staged inputs: " + (", ".join(paths) if paths else "none"))
    outputs = value.get("outputs")
    if isinstance(outputs, list):
        declarations = [
            f"{item.get('project_path')} ({'required' if item.get('required') else 'optional'})"
            for item in outputs
            if isinstance(item, dict) and isinstance(item.get("project_path"), str)
        ]
        lines.append(
            "  - Declared outputs: " + (", ".join(declarations) if declarations else "none")
        )
    lines.append("  - Write only declared outputs; undeclared runtime files are not collected.")
    return lines


def mailbox_tool_names(allowed_tools: tuple[str, ...]) -> list[str]:
    mailbox_tools = {
        "get_agent_inbox",
        "list_agent_thread_messages",
        "mark_agent_message_read",
        "send_agent_message",
    }
    return sorted(tool for tool in allowed_tools if tool in mailbox_tools)


def runtime_text_list(value: object, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    rendered: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            rendered.append(redact_sensitive_text(item))
        elif isinstance(item, dict) and item:
            safe_item = redact_sensitive_payload(item)
            rendered.append(json.dumps(safe_item, sort_keys=True, ensure_ascii=False))
        if len(rendered) >= limit:
            break
    return rendered


def runtime_mailbox_message_line(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    message_type = message.get("message_type")
    created_at = message.get("created_at")
    body_preview = message.get("body_preview")
    parts = [
        str(part)
        for part in (message_type, created_at, body_preview)
        if isinstance(part, str) and part
    ]
    return redact_sensitive_text(" | ".join(parts))
