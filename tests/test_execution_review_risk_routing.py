from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.agents.providers.resolution import ModelProviderResolutionService
from opsmesh.governance.audit.service import AuditService
from opsmesh.governance.reviews.approval_config import ApprovalConfiguration
from opsmesh.governance.reviews.configured import ConfiguredApprovalService
from opsmesh.governance.reviews.llm import LlmResourceReviewer, LlmReviewResult
from opsmesh.shared.config import Settings
from opsmesh.workspaces.management.schemas import WorkspaceCreateRequest, WorkspaceUpdateRequest


def reviewer(config):
    session = Mock()
    session.get.return_value = SimpleNamespace(settings={"approvals": config})
    return ConfiguredApprovalService(session, Settings(environment="test"))


@pytest.mark.parametrize(
    "action,name,args",
    [
        ("model_request", "model.run", {"input": "解释删除权限 password token"}),
        ("mcp_tool", "delete_record", {"password": "secret"}),
        ("product_tool", "write_artifact", {}),
        ("runtime_command", "rm", {"command": ["rm", "file"]}),
        ("resource", "mcp_server", {"command": "python"}),
    ],
)
def test_unconfigured_authorized_actions_never_invoke_review_model(monkeypatch, action, name, args):
    model = Mock(side_effect=AssertionError("No model review expected"))
    monkeypatch.setattr(LlmResourceReviewer, "review", model)
    result = reviewer({}).review(
        workspace_id=uuid4(), action=action, name=name, arguments=args, context={}
    )
    assert not result.required and not result.blocked
    model.assert_not_called()


def test_exact_server_and_command_selectors_and_deny_precedence():
    server = uuid4()
    service = reviewer(
        {
            "rules": [
                {
                    "id": "server-tool",
                    "action": "mcp_tool",
                    "server_id": str(server),
                    "name": "remove",
                    "decision": "review",
                },
                {
                    "id": "command-review",
                    "action": "runtime_command",
                    "command_prefix": ["git", "push"],
                    "decision": "review",
                },
                {
                    "id": "force-deny",
                    "action": "runtime_command",
                    "command_prefix": ["git", "push", "--force"],
                    "decision": "deny",
                },
            ]
        }
    )

    def command(argv):
        return service.review(
            workspace_id=uuid4(),
            action="runtime_command",
            name=argv[0],
            arguments={"command": argv},
            context={},
        )

    assert not command(["git", "status"]).required
    assert command(["git", "push"]).required
    assert command(["git", "push", "--force"]).blocked
    assert command(["sh", "-c", "git status && git push --force"]).blocked
    assert command(["sh", "-c", "git status && git push"]).required
    assert command(["sh", "-c", "git push $TARGET"]).required
    for target, expected in [(server, True), (uuid4(), False)]:
        result = service.review(
            workspace_id=uuid4(),
            action="mcp_tool",
            name="remove",
            arguments={},
            context={"mcp_server_id": str(target)},
        )
        assert result.required is expected


@pytest.mark.parametrize(
    "verdict,required,blocked",
    [
        ("approve", False, False),
        ("reject", False, True),
        ("needs_human", True, False),
    ],
)
def test_model_decision_not_overridden_by_risk_and_audited(
    monkeypatch,
    verdict,
    required,
    blocked,
):
    credential = uuid4()
    config = {
        "default": "review",
        "reviewer": "model",
        "model": {
            "model_provider_credential_id": str(credential),
            "model": "configured-review-model",
            "instructions": "Follow the user's authorized scope.",
        },
    }
    provider = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(ModelProviderResolutionService, "resolve_for_review", provider)
    model = Mock(
        return_value=LlmReviewResult(False, "high", ["scope.checked"], {"verdict": verdict})
    )
    monkeypatch.setattr(LlmResourceReviewer, "review", model)
    audit = Mock()
    monkeypatch.setattr(AuditService, "record_system_action", audit)
    result = reviewer(config).review(
        workspace_id=uuid4(),
        action="mcp_tool",
        name="remove",
        arguments={"api_key": "sk-never-expose"},
        context={},
    )
    assert (result.required, result.blocked) == (required, blocked)
    assert provider.call_args.kwargs["credential_id"] == credential
    assert provider.call_args.kwargs["review_model"] == "configured-review-model"
    assert "sk-never-expose" not in str(model.call_args)
    assert "sk-never-expose" not in str(audit.call_args)
    audit.assert_called_once()


@pytest.mark.parametrize(
    "fallback,required,blocked", [("human", True, False), ("deny", False, True)]
)
def test_model_failure_never_approves_or_exposes_exception(
    monkeypatch, fallback, required, blocked
):
    monkeypatch.setattr(
        ModelProviderResolutionService,
        "resolve_for_review",
        Mock(side_effect=TimeoutError("token=secret-value")),
    )
    monkeypatch.setattr(AuditService, "record_system_action", Mock())
    result = reviewer(
        {
            "default": "review",
            "reviewer": "model",
            "model": {
                "model_provider_credential_id": str(uuid4()),
                "model": "review-model",
                "instructions": "Check scope.",
                "on_error": fallback,
            },
        }
    ).review(
        workspace_id=uuid4(), action="model_request", name="model.run", arguments={}, context={}
    )
    assert (result.required, result.blocked) == (required, blocked)
    assert result.signals["verdict"] == "unavailable"
    assert "secret-value" not in str(result)


def test_create_and_update_reject_incomplete_or_unknown_approval_config():
    for schema, extra in [
        (WorkspaceCreateRequest, {"name": "a", "slug": "a"}),
        (WorkspaceUpdateRequest, {}),
    ]:
        with pytest.raises(ValidationError):
            schema(**extra, settings={"approvals": {"reviewer": "model"}})
        with pytest.raises(ValidationError):
            schema(**extra, settings={"approvals": {"rulez": []}})
    with pytest.raises(ValidationError):
        ApprovalConfiguration.model_validate(
            {
                "rules": [
                    {
                        "id": "bad",
                        "action": "runtime_command",
                        "command_prefix": [],
                        "decision": "review",
                    }
                ]
            }
        )
