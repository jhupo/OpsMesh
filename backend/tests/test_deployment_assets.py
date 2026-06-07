from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfile_defines_non_root_api_runtime() -> None:
    dockerfile = read_repo_file("Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "pip install ." in dockerfile
    assert "sed -i 's/\\r$//'" in dockerfile
    assert "USER chaincloud" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "backend.app.main:create_app" in dockerfile


def test_compose_declares_api_worker_and_dependencies() -> None:
    compose = read_repo_file("docker-compose.yml")

    for service in ("api:", "worker:", "postgres:", "redis:"):
        assert service in compose
    assert "CHAINCLOUD_DATABASE_URL" in compose
    assert "CHAINCLOUD_REDIS_URL" in compose
    assert "CHAINCLOUD_RUN_MIGRATIONS: \"false\"" in compose
    assert "/api/v1/health/ready" in compose


def test_env_template_lists_required_runtime_settings() -> None:
    env_example = read_repo_file(".env.example")

    for setting in (
        "CHAINCLOUD_ENABLE_API_DOCS",
        "CHAINCLOUD_INTERNAL_API_TOKEN",
        "CHAINCLOUD_TOKEN_HASH_PEPPER",
        "CHAINCLOUD_AGENT_RUNNER_BACKEND",
        "CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET",
        "CHAINCLOUD_CREDENTIAL_ENCRYPTION_KEY_ID",
        "CHAINCLOUD_WORKER_HEARTBEAT_TOKEN",
        "CHAINCLOUD_READINESS_WORKER_CHECK_ENABLED",
        "CHAINCLOUD_EXTERNAL_CALL_MAX_ATTEMPTS",
        "CHAINCLOUD_AUDIT_EVENT_WORM_ENABLED",
        "CHAINCLOUD_POSTGRES_PASSWORD",
    ):
        assert setting in env_example
    assert "CHAINCLOUD_AGENT_RUNNER_BACKEND=provider_dispatching" in env_example


def test_deployment_docs_cover_processes_and_production_guards() -> None:
    docs = read_repo_file("docs/backend-deployment.md")

    assert "API process" in docs
    assert "Worker process" in docs
    assert "Server Test Stack" in docs
    assert "minimal monitoring stack" in docs
    assert "CHAINCLOUD_SMOKE_MONITORING=true" in docs
    assert "scripts/server-smoke-test.sh" in docs
    assert "scripts/openai-gateway-smoke.py" in docs
    assert "--dry-run" in docs
    assert "--allow-external-provider-call" in docs
    assert "openai_smoke" in docs
    assert "CHAINCLOUD_AGENT_RUNNER_BACKEND=provider_dispatching" in docs
    assert "CHAINCLOUD_ENABLE_API_DOCS=false" in docs
    assert "CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET" in docs
    assert "CHAINCLOUD_RUN_MIGRATIONS=false" in docs


def test_server_compose_reuses_external_database_network() -> None:
    compose = read_repo_file("deploy/server/docker-compose.backend.yml")

    assert "api:" in compose
    assert "worker:" in compose
    assert "prometheus:" in compose
    assert "alertmanager:" in compose
    assert "grafana:" in compose
    assert "postgres:" not in compose
    assert "redis:" not in compose
    assert "external: true" in compose
    assert "name: ${CHAINCLOUD_BACKEND_NETWORK:-chaincloud_default}" in compose
    assert "${CHAINCLOUD_API_BIND:-127.0.0.1}:${CHAINCLOUD_API_PORT:-8000}:8000" in compose
    assert "CHAINCLOUD_RUN_MIGRATIONS: \"false\"" in compose
    assert "/api/v1/health/ready" in compose
    assert "CHAINCLOUD_MONITORING_DIR" in compose
    assert "prometheus.yml:/etc/prometheus/prometheus.yml:ro" in compose
    assert (
        "alert-rules.yml:/etc/prometheus/rules/chaincloud.yml:ro"
        in compose
    )
    assert "${CHAINCLOUD_GRAFANA_BIND:-127.0.0.1}:${CHAINCLOUD_GRAFANA_PORT:-3000}:3000" in compose


def test_server_env_template_uses_shared_runtime_services() -> None:
    env_example = read_repo_file("deploy/server/env.example")

    assert "chaincloud-postgres:5432" in env_example
    assert "chaincloud-redis:6379" in env_example
    assert "CHAINCLOUD_BACKEND_NETWORK=chaincloud_default" in env_example
    assert "CHAINCLOUD_RELEASE_DIR=/opt/chaincloud-app/current" in env_example
    assert "CHAINCLOUD_ENV_FILE=/opt/chaincloud-app/.env" in env_example
    assert (
        "CHAINCLOUD_MONITORING_DIR=/opt/chaincloud-app/current/deploy/server/monitoring"
        in env_example
    )
    assert "CHAINCLOUD_WORKER_HEARTBEAT_TOKEN=replace-with-random-token" in env_example
    assert "CHAINCLOUD_READINESS_WORKER_CHECK_ENABLED=false" in env_example
    assert "CHAINCLOUD_EXTERNAL_CALL_MAX_ATTEMPTS=2" in env_example
    assert "CHAINCLOUD_AUDIT_EVENT_WORM_ENABLED=true" in env_example
    assert "CHAINCLOUD_PROMETHEUS_PORT=9090" in env_example
    assert "CHAINCLOUD_ALERTMANAGER_PORT=9093" in env_example
    assert "CHAINCLOUD_GRAFANA_PORT=3000" in env_example
    assert "CHAINCLOUD_GRAFANA_ADMIN_PASSWORD=replace-with-random-password" in env_example
    assert "replace-with-random-token" in env_example
    assert "chaincloud:chaincloud" not in env_example


def test_server_smoke_script_checks_health_and_migrations() -> None:
    smoke_script = read_repo_file("scripts/server-smoke-test.sh")

    assert "#!/usr/bin/env sh" in smoke_script
    assert "/api/v1/health/ready" in smoke_script
    assert "docker compose --env-file" in smoke_script
    assert "alembic current" in smoke_script
    assert "CHAINCLOUD_SMOKE_MONITORING" in smoke_script
    assert "/-/ready" in smoke_script
    assert "/api/health" in smoke_script


def test_openai_gateway_smoke_script_uses_env_key_and_marker() -> None:
    smoke_script = read_repo_file("scripts/openai-gateway-smoke.py")

    assert "#!/usr/bin/env python" in smoke_script
    assert "OPENAI_API_KEY" in smoke_script
    assert "OPENAI_SMOKE_BASE_URL" in smoke_script
    assert "normalize_openai_compatible_base_url" in smoke_script
    assert "--dry-run" in smoke_script
    assert "--allow-external-provider-call" in smoke_script
    assert "openai_smoke" in smoke_script
    assert "sk-" not in smoke_script


def test_backend_ci_runs_tests_and_alembic_drift_check() -> None:
    workflow = read_repo_file(".github/workflows/backend-ci.yml")

    assert "postgres:16" in workflow
    assert "uv run pytest" in workflow
    assert "uv run ruff check ." in workflow
    assert "uv run alembic upgrade head" in workflow
    assert "uv run alembic check" in workflow
    assert "docker build -t chaincloud-backend:${{ github.sha }} ." in workflow
    assert "docker run --rm --entrypoint python" in workflow
    assert "docker compose -f docker-compose.yml config --quiet" in workflow
    assert "docker compose -f deploy/server/docker-compose.backend.yml config --quiet" in workflow
    assert "anchore/sbom-action" in workflow
    assert "aquasecurity/trivy-action" in workflow
    assert 'exit-code: "1"' in workflow


def test_monitoring_assets_define_alerts_and_grafana_provisioning() -> None:
    prometheus = read_repo_file("deploy/server/monitoring/prometheus.yml")
    alert_rules = read_repo_file("deploy/server/monitoring/alert-rules.yml")
    alertmanager = read_repo_file("deploy/server/monitoring/alertmanager.yml")
    datasource = read_repo_file(
        "deploy/server/monitoring/grafana/provisioning/datasources/prometheus.yml"
    )
    dashboard_provider = read_repo_file(
        "deploy/server/monitoring/grafana/provisioning/dashboards/dashboards.yml"
    )
    dashboard = read_repo_file(
        "deploy/server/monitoring/grafana/dashboards/chaincloud-overview.json"
    )

    assert "job_name: chaincloud-api" in prometheus
    assert "metrics_path: /api/v1/metrics" in prometheus
    assert "alertmanager:9093" in prometheus
    assert "/etc/prometheus/rules/*.yml" in prometheus

    for alert_name in (
        "ChainCloudApiDown",
        "ChainCloudHighHttp5xxRate",
        "ChainCloudQueueBacklogHigh",
        "ChainCloudDeadLettersPresent",
        "ChainCloudNoOnlineWorkersWithBacklog",
        "ChainCloudWorkerStale",
        "ChainCloudRuntimeSaturationHigh",
        "ChainCloudRuntimeQuotaHigh",
    ):
        assert alert_name in alert_rules
    assert "chaincloud_http_requests_total" in alert_rules
    assert "chaincloud_queue_jobs" in alert_rules
    assert "chaincloud_workers" in alert_rules
    assert "chaincloud_runtime_saturation_ratio" in alert_rules

    assert "receiver: chaincloud-operators" in alertmanager
    assert "url: http://prometheus:9090" in datasource
    assert "path: /var/lib/grafana/dashboards" in dashboard_provider
    assert '"uid": "chaincloud-control-plane"' in dashboard
    assert "chaincloud_runtime_space_quota_usage_ratio" in dashboard
