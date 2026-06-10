#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "encrypted_api_key",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
SECRET_VALUES: set[str] = set()


@dataclass(frozen=True)
class HttpTeamE2EConfig:
    api_url: str
    provider_api_key: str
    provider_base_url: str
    model: str
    provider: str
    model_api: str | None
    timeout_seconds: int
    poll_seconds: float
    suffix: str


class ChainCloudHTTPClient:
    def __init__(self, config: HttpTeamE2EConfig) -> None:
        self.config = config
        self.client = httpx.Client(
            base_url=config.api_url.rstrip("/"),
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self.request_log: list[dict[str, object]] = []
        self.token: str | None = None

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        expected: set[int] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        started = time.monotonic()
        response = self.client.request(method, path, json=json_body, headers=headers)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self.request_log.append(
            {
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
            }
        )
        accepted = expected or {200}
        if response.status_code not in accepted:
            raise RuntimeError(
                json.dumps(
                    {
                        "message": "HTTP request failed",
                        "method": method,
                        "path": path,
                        "status_code": response.status_code,
                        "response": _safe_response_json(response),
                    },
                    ensure_ascii=False,
                )
            )
        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"items": payload}


def main() -> int:
    config = _config_from_env_and_args()
    client = ChainCloudHTTPClient(config)
    resources: dict[str, Any] = {}
    exit_code = 1
    try:
        resources = _create_http_team(client, config)
        summary = _poll_and_summarize(client, config, resources)
        print(json.dumps(_redact(summary), ensure_ascii=False, sort_keys=True))
        exit_code = 0 if summary.get("status") == "ok" else 1
    except Exception as exc:
        partial_summary = _partial_summary(client, resources)
        print(
            json.dumps(
                _redact(
                    {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "resources": _resource_ids(resources),
                        "partial_summary": partial_summary,
                        "requests": client.request_log,
                    }
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        exit_code = 1
    finally:
        client.close()
    return exit_code


def _create_http_team(
    client: ChainCloudHTTPClient,
    config: HttpTeamE2EConfig,
) -> dict[str, Any]:
    email = f"http-e2e-{config.suffix}@example.com"
    password = f"Http-E2E-{uuid4().hex}"
    client.request(
        "POST",
        "/auth/register",
        json_body={
            "email": email,
            "display_name": "HTTP E2E Owner",
            "password": password,
        },
        expected={201},
    )
    login = client.request(
        "POST",
        "/auth/login",
        json_body={"email": email, "password": password},
    )
    token = login.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Login response did not include an API token")
    client.token = token

    workspace = client.request(
        "POST",
        "/workspaces",
        json_body={
            "name": f"HTTP Real E2E {config.suffix}",
            "slug": f"http-real-e2e-{config.suffix}",
            "settings": {},
        },
        expected={201},
    )
    workspace_id = _require_id(workspace, "workspace")
    provider_payload: dict[str, object] = {
        "name": "HTTP E2E temporary provider",
        "provider": config.provider,
        "api_key": config.provider_api_key,
        "default_model": config.model,
        "base_url": config.provider_base_url,
        "is_default": True,
        "budget_metadata": {},
    }
    if config.model_api is not None:
        provider_payload["model_api"] = config.model_api
    credential = client.request(
        "POST",
        f"/workspaces/{workspace_id}/model-provider-credentials",
        json_body=provider_payload,
        expected={201},
    )
    credential_id = _require_id(credential, "credential")

    shared_model_settings = (
        {"temperature": 0, "max_tokens": 80}
        if config.model_api is None
        else {"temperature": 0, "max_tokens": 80, "model_api": config.model_api}
    )
    manager = _create_agent(
        client,
        workspace_id,
        credential_id,
        name="HTTP Real PM",
        role="project_manager",
        model=config.model,
        model_settings=shared_model_settings,
        instructions=(
            "Coordinate the team. Keep responses under 40 words. Approve final delivery "
            "when the specialist steps have non-empty outputs."
        ),
    )
    researcher = _create_agent(
        client,
        workspace_id,
        credential_id,
        name="HTTP Real Researcher",
        role="researcher",
        model=config.model,
        model_settings=shared_model_settings,
        instructions="Return a short factual research summary under 40 words.",
    )
    analyst = _create_agent(
        client,
        workspace_id,
        credential_id,
        name="HTTP Real Analyst",
        role="analyst",
        model=config.model,
        model_settings=shared_model_settings,
        instructions="Return a short analysis under 40 words.",
    )
    team = client.request(
        "POST",
        f"/workspaces/{workspace_id}/teams",
        json_body={
            "name": f"HTTP Real Team {config.suffix}",
            "team_type": "research",
            "description": "HTTP API real-provider E2E team",
            "manager_agent_profile_id": manager["id"],
            "coordination_rules": {},
            "default_task_policy": {},
        },
        expected={201},
    )
    team_id = _require_id(team, "team")
    _create_member(
        client,
        workspace_id,
        team_id,
        researcher["id"],
        role="researcher",
        department="Research",
        skill_weights={"market_research": 1.0},
        order_index=1,
    )
    _create_member(
        client,
        workspace_id,
        team_id,
        analyst["id"],
        role="analyst",
        department="Analysis",
        skill_weights={"analysis": 1.0},
        order_index=2,
    )
    task = client.request(
        "POST",
        f"/workspaces/{workspace_id}/tasks",
        json_body={
            "agent_team_id": team_id,
            "domain_type": "market_research",
            "title": "HTTP real model team dispatch smoke",
            "description": (
                "Create a concise live E2E proof that the HTTP API can create a team, "
                "dispatch specialist work, and surface execution evidence."
            ),
            "priority": 9,
            "input": {
                "work_packages": [
                    {
                        "package_id": "http-real-research",
                        "title": "HTTP real research",
                        "required_role": "researcher",
                        "required_skills": ["market_research"],
                    },
                    {
                        "package_id": "http-real-analysis",
                        "title": "HTTP real analysis",
                        "required_role": "analyst",
                        "required_skills": ["analysis"],
                        "depends_on": ["http-real-research"],
                    },
                ]
            },
        },
        expected={201},
    )
    task_id = _require_id(task, "task")
    execution_loop = client.request(
        "POST",
        f"/workspaces/{workspace_id}/teams/{team_id}/execution-loop/run",
        json_body={
            "dry_run": False,
            "apply_command_center_actions": True,
            "enqueue_runs": True,
            "finalize_ready_tasks": True,
            "sources": [],
            "actions": [],
            "reason": "http-real-e2e",
            "metadata": {"script": "real-team-http-e2e"},
        },
    )
    return {
        "workspace": workspace,
        "credential": credential,
        "agents": [manager, researcher, analyst],
        "team": team,
        "task": task,
        "execution_loop": execution_loop,
        "workspace_id": workspace_id,
        "team_id": team_id,
        "task_id": task_id,
    }


def _create_agent(
    client: ChainCloudHTTPClient,
    workspace_id: str,
    credential_id: str,
    *,
    name: str,
    role: str,
    model: str,
    model_settings: dict[str, object],
    instructions: str,
) -> dict[str, Any]:
    return client.request(
        "POST",
        f"/workspaces/{workspace_id}/agents",
        json_body={
            "name": name,
            "role": role,
            "description": "",
            "instructions": instructions,
            "model": model,
            "model_provider_credential_id": credential_id,
            "model_settings": model_settings,
            "capabilities": {},
            "skills": {},
            "tool_policy": {},
            "runtime_policy": {},
            "memory_policy": {},
            "approval_policy": {},
        },
        expected={201},
    )


def _create_member(
    client: ChainCloudHTTPClient,
    workspace_id: str,
    team_id: str,
    agent_id: str,
    *,
    role: str,
    department: str,
    skill_weights: dict[str, object],
    order_index: int,
) -> dict[str, Any]:
    return client.request(
        "POST",
        f"/workspaces/{workspace_id}/teams/{team_id}/members",
        json_body={
            "agent_profile_id": agent_id,
            "team_role": role,
            "department": department,
            "responsibilities": [f"Handle {role} work"],
            "skill_weights": skill_weights,
            "availability": {},
            "max_concurrent_tasks": 1,
            "accepts_tasks": True,
            "is_required": True,
            "order_index": order_index,
        },
        expected={201},
    )


def _poll_and_summarize(
    client: ChainCloudHTTPClient,
    config: HttpTeamE2EConfig,
    resources: dict[str, Any],
) -> dict[str, object]:
    workspace_id = str(resources["workspace_id"])
    task_id = str(resources["task_id"])
    deadline = time.monotonic() + config.timeout_seconds
    last_live_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_live_status = client.request(
            "GET",
            f"/workspaces/{workspace_id}/tasks/{task_id}/live-status?message_limit=100",
        )
        task = last_live_status.get("task")
        summary = last_live_status.get("summary")
        task_status = task.get("status") if isinstance(task, dict) else None
        active_runs = summary.get("active_run_count") if isinstance(summary, dict) else None
        if task_status in TERMINAL_TASK_STATUSES and active_runs == 0:
            break
        time.sleep(config.poll_seconds)
    return _build_summary(
        client,
        resources,
        last_live_status,
        timed_out=time.monotonic() >= deadline,
    )


def _build_summary(
    client: ChainCloudHTTPClient,
    resources: dict[str, Any],
    live_status: dict[str, Any],
    *,
    timed_out: bool,
) -> dict[str, object]:
    workspace_id = str(resources["workspace_id"])
    task_id = str(resources["task_id"])
    team_id = str(resources["team_id"])
    task = live_status.get("task") if isinstance(live_status.get("task"), dict) else {}
    task_status = task.get("status") if isinstance(task, dict) else None
    timeline = _try_request(
        client,
        f"/workspaces/{workspace_id}/tasks/{task_id}/timeline?limit=200",
    )
    diagnostics = _required_get(
        client,
        f"/workspaces/{workspace_id}/tasks/{task_id}/execution-diagnostics",
    )
    messages = _required_get(
        client,
        f"/workspaces/{workspace_id}/tasks/{task_id}/messages?limit=100",
    )
    runs = _required_get(client, f"/workspaces/{workspace_id}/runs?limit=100")
    run_items = runs.get("items") if isinstance(runs.get("items"), list) else []
    task_run_items = [
        run
        for run in run_items
        if isinstance(run, dict) and run.get("task_id") == task_id
    ]
    run_events = [
        {
            "run_id": run.get("id"),
            "events": _required_get(
                client,
                f"/workspaces/{workspace_id}/runs/{run.get('id')}/events?limit=100",
            ).get("items", []),
        }
        for run in task_run_items
        if isinstance(run, dict) and isinstance(run.get("id"), str)
    ]
    provider_usage_audit = _required_get(
        client,
        f"/workspaces/{workspace_id}/model-provider-credentials/usage-audit?limit=50",
    )
    operations = {
        "queue_metrics": _try_request(
            client,
            f"/workspaces/{workspace_id}/operations/queue-metrics",
        ),
        "run_events": _try_request(
            client,
            f"/workspaces/{workspace_id}/operations/run-events?limit=50",
        ),
        "team_console": _try_request(
            client,
            f"/workspaces/{workspace_id}/teams/{team_id}/operations-console",
        ),
        "provider_usage_audit": provider_usage_audit,
    }
    evidence = _evaluate_evidence(
        resources=resources,
        task_id=task_id,
        task_status=task_status,
        timed_out=timed_out,
        run_items=task_run_items,
        run_events=run_events,
        provider_usage_audit=provider_usage_audit,
    )
    return {
        "status": "ok" if evidence["passed"] else "failed",
        "timed_out": timed_out,
        "evidence": evidence,
        "resources": _resource_ids(resources),
        "created": {
            "workspace": _select(resources["workspace"], "id", "name", "slug", "status"),
            "team": _select(resources["team"], "id", "name", "team_type", "status"),
            "agents": [
                _select(agent, "id", "name", "role", "model", "model_provider")
                for agent in resources["agents"]
            ],
            "credential": _select(
                resources["credential"],
                "id",
                "provider",
                "default_model",
                "model_api",
                "base_url_configured",
                "base_url_host",
                "health_status",
                "status",
            ),
        },
        "task": _select(
            task if isinstance(task, dict) else {},
            "id",
            "title",
            "status",
            "priority",
            "domain_type",
        ),
        "task_breakdown": live_status.get("steps", []),
        "live_summary": live_status.get("summary", {}),
        "messages": messages.get("items", []),
        "timeline": timeline,
        "execution_diagnostics": diagnostics,
        "runs": task_run_items,
        "run_events": run_events,
        "operations": operations,
        "requests": client.request_log,
    }


def _partial_summary(
    client: ChainCloudHTTPClient,
    resources: dict[str, Any],
) -> dict[str, object] | None:
    if not resources or "workspace_id" not in resources or "task_id" not in resources:
        return None
    try:
        live_status = client.request(
            "GET",
            f"/workspaces/{resources['workspace_id']}/tasks/{resources['task_id']}/live-status",
        )
        return _build_summary(client, resources, live_status, timed_out=False)
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


def _config_from_env_and_args() -> HttpTeamE2EConfig:
    parser = argparse.ArgumentParser(
        description="Run a real-provider team E2E through ChainCloud's public HTTP API."
    )
    parser.add_argument("--api-url", default=os.environ.get("CHAINCLOUD_HTTP_E2E_API_URL"))
    parser.add_argument("--api-key", default=os.environ.get("CHAINCLOUD_HTTP_E2E_API_KEY"))
    parser.add_argument("--base-url", default=os.environ.get("CHAINCLOUD_HTTP_E2E_BASE_URL"))
    parser.add_argument(
        "--model",
        default=os.environ.get("CHAINCLOUD_HTTP_E2E_MODEL", "gpt-5.5"),
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("CHAINCLOUD_HTTP_E2E_PROVIDER", "openai-compatible"),
    )
    parser.add_argument(
        "--model-api",
        default=os.environ.get("CHAINCLOUD_HTTP_E2E_MODEL_API", "chat_completions"),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("CHAINCLOUD_HTTP_E2E_TIMEOUT_SECONDS", "240")),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("CHAINCLOUD_HTTP_E2E_POLL_SECONDS", "5")),
    )
    args = parser.parse_args()
    missing = [
        name
        for name, value in {
            "CHAINCLOUD_HTTP_E2E_API_URL or --api-url": args.api_url,
            "CHAINCLOUD_HTTP_E2E_API_KEY or --api-key": args.api_key,
            "CHAINCLOUD_HTTP_E2E_BASE_URL or --base-url": args.base_url,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
    model_api = str(args.model_api).strip() if args.model_api is not None else None
    SECRET_VALUES.add(str(args.api_key))
    return HttpTeamE2EConfig(
        api_url=str(args.api_url),
        provider_api_key=str(args.api_key),
        provider_base_url=str(args.base_url),
        model=str(args.model),
        provider=str(args.provider),
        model_api=model_api or None,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        suffix=uuid4().hex[:10],
    )


def _try_request(client: ChainCloudHTTPClient, path: str) -> dict[str, Any]:
    try:
        return client.request("GET", path)
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


def _required_get(client: ChainCloudHTTPClient, path: str) -> dict[str, Any]:
    payload = client.request("GET", path)
    if payload.get("status") == "unavailable":
        raise RuntimeError(f"Required evidence endpoint unavailable: {path}")
    return payload


def _evaluate_evidence(
    *,
    resources: dict[str, Any],
    task_id: str,
    task_status: object,
    timed_out: bool,
    run_items: list[Any],
    run_events: list[dict[str, object]],
    provider_usage_audit: dict[str, Any],
) -> dict[str, object]:
    required_event_types = {
        "run.claimed",
        "model.request_started",
        "model.response_received",
        "run.completed",
    }
    success_event_types = {"model_provider.used"}
    observed_event_types = {
        event.get("event_type")
        for item in run_events
        for event in item.get("events", [])
        if isinstance(item.get("events"), list) and isinstance(event, dict)
    }
    missing_event_types = sorted(required_event_types - observed_event_types)
    missing_success_event_types = sorted(success_event_types - observed_event_types)
    fallback_event_types = sorted(
        event_type
        for event_type in observed_event_types
        if isinstance(event_type, str) and "fallback" in event_type
    )
    credential_id = _resource_ids(resources).get("credential_id")
    audit_items = provider_usage_audit.get("items")
    audit_items = audit_items if isinstance(audit_items, list) else []
    matching_usage = [
        item
        for item in audit_items
        if isinstance(item, dict)
        and item.get("action") == "model_provider.used"
        and item.get("task_id") == task_id
    ]
    matching_failures = [
        item
        for item in audit_items
        if isinstance(item, dict)
        and item.get("action") == "model_provider.request_failed"
        and item.get("task_id") == task_id
    ]
    used_created_credential = any(
        item.get("credential_id") == credential_id
        and item.get("fallback_selected") is not True
        for item in matching_usage
        if isinstance(item, dict)
    )
    failed_created_credential = any(
        item.get("credential_id") == credential_id
        for item in matching_failures
        if isinstance(item, dict)
    )
    provider_request_failed = (
        "model.request_failed" in observed_event_types or bool(matching_failures)
    )
    failures: list[str] = []
    if timed_out:
        failures.append("timed_out")
    if provider_request_failed:
        failures.append("provider_request_failed")
    elif task_status != "completed":
        failures.append("task_not_completed")
    if not run_items:
        failures.append("no_runs_for_task")
    if missing_event_types:
        failures.append("missing_required_run_events")
    if missing_success_event_types:
        failures.append("missing_success_run_events")
    if fallback_event_types:
        failures.append("provider_fallback_event_observed")
    if not matching_usage and not matching_failures:
        failures.append("missing_model_provider_usage_audit")
    if matching_usage and not used_created_credential:
        failures.append("model_provider_usage_did_not_match_created_credential")
    if matching_failures and not failed_created_credential:
        failures.append("model_provider_failure_did_not_match_created_credential")
    return {
        "passed": not failures,
        "failures": failures,
        "diagnosis": "provider_request_failed"
        if provider_request_failed
        else "completed"
        if not failures
        else "platform_incomplete",
        "required_event_types": sorted(required_event_types),
        "success_event_types": sorted(success_event_types),
        "observed_event_types": sorted(
            event_type for event_type in observed_event_types if isinstance(event_type, str)
        ),
        "missing_event_types": missing_event_types,
        "missing_success_event_types": missing_success_event_types,
        "fallback_event_types": fallback_event_types,
        "matching_model_provider_usage_count": len(matching_usage),
        "matching_model_provider_failure_count": len(matching_failures),
        "used_created_credential": used_created_credential,
        "failed_created_credential": failed_created_credential,
    }


def _require_id(payload: dict[str, Any], label: str) -> str:
    value = payload.get("id")
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} response did not include an id")
    return value


def _select(payload: dict[str, Any], *keys: str) -> dict[str, object]:
    return {key: payload.get(key) for key in keys if key in payload}


def _resource_ids(resources: dict[str, Any]) -> dict[str, object]:
    return {
        "workspace_id": resources.get("workspace_id"),
        "team_id": resources.get("team_id"),
        "task_id": resources.get("task_id"),
        "agent_ids": [
            agent.get("id")
            for agent in resources.get("agents", [])
            if isinstance(agent, dict) and agent.get("id")
        ],
        "credential_id": resources.get("credential", {}).get("id")
        if isinstance(resources.get("credential"), dict)
        else None,
    }


def _safe_response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def _redact(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = key.lower().replace("-", "_")
            if normalized_key in SECRET_KEYS or normalized_key.endswith("_key"):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        redacted_value = value
        for secret in SECRET_VALUES:
            if secret:
                redacted_value = redacted_value.replace(secret, "[redacted]")
        return redacted_value
    return value


if __name__ == "__main__":
    raise SystemExit(main())
