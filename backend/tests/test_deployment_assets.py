from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfile_defines_non_root_api_runtime() -> None:
    dockerfile = read_repo_file("Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "pip install ." in dockerfile
    assert "sed -i 's/\\r$//'" in dockerfile
    assert "adduser --system --ingroup opsmesh opsmesh" in dockerfile
    assert "/app/.opsmesh-storage" in dockerfile
    assert "USER opsmesh" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "backend.app.main:create_app" in dockerfile


def test_runtime_dockerfile_installs_only_isolated_runtime_package() -> None:
    dockerfile = read_repo_file("Dockerfile.runtime")
    runtime_project = read_repo_file("runtime/pyproject.toml")

    assert "FROM python:3.12-slim" in dockerfile
    assert "COPY runtime/pyproject.toml" in dockerfile
    assert "COPY runtime/opsmesh_runtime" in dockerfile
    assert "python -m opsmesh_runtime.mcp_stdio_client --check" in dockerfile
    assert "USER opsmesh-runtime" in dockerfile
    assert "WORKDIR /workspace" in dockerfile
    assert "backend/app" not in dockerfile
    assert '"mcp==1.27.1"' in runtime_project


def test_compose_declares_api_worker_and_dependencies() -> None:
    compose = read_repo_file("docker-compose.yml")

    for service in ("api:", "worker:", "postgres:", "redis:"):
        assert service in compose
    assert "OPSMESH_DATABASE_URL" in compose
    assert "OPSMESH_REDIS_URL" in compose
    assert "/app/.opsmesh-storage" in compose
    assert "OPSMESH_RUN_MIGRATIONS: \"false\"" in compose
    assert "/api/v1/health/ready" in compose


def test_env_template_lists_required_runtime_settings() -> None:
    env_example = read_repo_file(".env.example")

    for setting in (
        "OPSMESH_ENABLE_API_DOCS",
        "OPSMESH_INTERNAL_API_TOKEN",
        "OPSMESH_TOKEN_HASH_PEPPER",
        "OPSMESH_CREDENTIAL_ENCRYPTION_SECRET",
        "OPSMESH_CREDENTIAL_ENCRYPTION_KEY_ID",
        "OPSMESH_WORKER_HEARTBEAT_TOKEN",
        "OPSMESH_READINESS_WORKER_CHECK_ENABLED",
        "OPSMESH_EXTERNAL_CALL_MAX_ATTEMPTS",
        "OPSMESH_AUDIT_EVENT_WORM_ENABLED",
        "OPSMESH_POSTGRES_PASSWORD",
    ):
        assert setting in env_example
    assert "OPSMESH_SERVICE_NAME=opsmesh-backend" in env_example
    assert "OPSMESH_STORAGE_ROOT=.opsmesh-storage" in env_example
    assert "OPSMESH_AGENT_RUNNER_BACKEND" not in env_example
    assert 'OPSMESH_RUNTIME_ALLOWED_IMAGES=["opsmesh-runtime:local"]' in env_example


def test_deployment_docs_cover_processes_and_production_guards() -> None:
    docs = read_repo_file("docs/backend-deployment.md")

    assert "API process" in docs
    assert "Worker process" in docs
    assert "VPS Layout" in docs
    assert "systemd Services" in docs
    assert "Docker is still required on the host for dangerous task runtimes" in docs
    assert "the API process must not be able to control the Docker daemon" in docs
    assert "minimal monitoring stack" in docs
    assert "OPSMESH_SMOKE_MONITORING=true" in docs
    assert "scripts/server-smoke-test.sh" in docs
    assert "opsmesh-api.service" in docs
    assert "opsmesh-worker.service" in docs
    assert "opsmesh-api" in docs
    assert "opsmesh-worker" in docs
    assert "sudo usermod -aG docker opsmesh-worker" in docs
    assert "sudo usermod -aG docker opsmesh\n" not in docs
    assert (
        "Description=OpsMesh API\n"
        "After=network-online.target postgresql.service redis-server.service\n"
        in docs
    )
    assert "uv sync" in docs
    assert "alembic upgrade head" in docs
    assert "systemctl restart" not in docs
    assert "docker compose -f deploy/server/docker-compose.backend.yml" not in docs
    assert "OPSMESH_COMPOSE_FILE" not in docs
    assert "docker login ghcr.io" not in docs
    assert "scripts/openai-gateway-smoke.py" in docs
    assert "--dry-run" in docs
    assert "--allow-external-provider-call" in docs
    assert "openai_smoke" in docs
    assert "real provider-dispatching runner" in docs
    assert "OPSMESH_AGENT_RUNNER_BACKEND" not in docs
    assert "OPSMESH_ENABLE_API_DOCS=false" in docs
    assert "OPSMESH_CREDENTIAL_ENCRYPTION_SECRET" in docs
    assert "OPSMESH_RUN_MIGRATIONS=false" not in docs


def test_server_docs_do_not_publish_backend_compose_deploy_path() -> None:
    docs = read_repo_file("docs/backend-deployment.md")
    update_script = read_repo_file("scripts/server-update.sh")
    smoke_script = read_repo_file("scripts/server-smoke-test.sh")

    for asset in (docs, update_script, smoke_script):
        assert "docker-compose.backend.yml" not in asset
        assert "OPSMESH_BACKEND_IMAGE" not in asset
        assert "OPSMESH_BACKEND_NETWORK" not in asset
    assert "docker compose --env-file" not in update_script
    assert "docker compose --env-file" not in smoke_script


def test_systemd_units_keep_api_out_of_docker_group() -> None:
    api_unit = read_repo_file("deploy/server/systemd/opsmesh-api.service")
    worker_unit = read_repo_file("deploy/server/systemd/opsmesh-worker.service")

    assert "User=opsmesh-api" in api_unit
    assert "docker.service" not in api_unit
    assert "SupplementaryGroups=docker" not in api_unit
    assert "User=opsmesh-worker" in worker_unit
    assert "docker.service" in worker_unit
    assert "SupplementaryGroups=docker" in worker_unit


def test_server_env_template_uses_shared_runtime_services() -> None:
    env_example = read_repo_file("deploy/server/env.example")

    assert "OPSMESH_ENVIRONMENT=production" in env_example
    assert "OPSMESH_ENABLE_API_DOCS=false" in env_example
    assert "127.0.0.1:5432" in env_example
    assert "127.0.0.1:6379" in env_example
    assert "OPSMESH_BACKEND_NETWORK" not in env_example
    assert "OPSMESH_BACKEND_IMAGE" not in env_example
    assert "OPSMESH_ROOT=/opt/opsmesh" in env_example
    assert "OPSMESH_RELEASES_DIR=/opt/opsmesh/releases" in env_example
    assert "OPSMESH_CURRENT_LINK=/opt/opsmesh/current" in env_example
    assert "OPSMESH_API_SERVICE=opsmesh-api" in env_example
    assert "OPSMESH_WORKER_SERVICE=opsmesh-worker" in env_example
    assert 'OPSMESH_UV_SYNC_ARGS="--frozen --no-dev"' in env_example
    assert "OPSMESH_RELEASE_DIR=/opt/opsmesh/current" in env_example
    assert "OPSMESH_ENV_FILE=/opt/opsmesh/.env" in env_example
    assert (
        "OPSMESH_MONITORING_DIR=/opt/opsmesh/current/deploy/server/monitoring"
        in env_example
    )
    assert "OPSMESH_WORKER_HEARTBEAT_TOKEN=replace-with-random-token" in env_example
    assert "OPSMESH_SERVICE_NAME=opsmesh-backend" in env_example
    assert "OPSMESH_STORAGE_ROOT=/var/lib/opsmesh/storage" in env_example
    assert "OPSMESH_READINESS_WORKER_CHECK_ENABLED=true" in env_example
    assert "OPSMESH_EXTERNAL_CALL_MAX_ATTEMPTS=2" in env_example
    assert "OPSMESH_AUDIT_EVENT_WORM_ENABLED=true" in env_example
    assert 'OPSMESH_RUNTIME_ALLOWED_IMAGES=["opsmesh-runtime:local"]' in env_example
    assert "OPSMESH_PROMETHEUS_PORT=9090" in env_example
    assert "OPSMESH_ALERTMANAGER_PORT=9093" in env_example
    assert "OPSMESH_GRAFANA_PORT=3000" in env_example
    assert "OPSMESH_GRAFANA_ADMIN_PASSWORD=replace-with-random-password" in env_example
    assert "replace-with-random-token" in env_example
    assert "opsmesh:opsmesh" not in env_example


def test_server_smoke_script_checks_health_and_migrations() -> None:
    smoke_script = read_repo_file("scripts/server-smoke-test.sh")

    assert "#!/usr/bin/env sh" in smoke_script
    assert "/api/v1/health/ready" in smoke_script
    assert "systemctl" in smoke_script
    assert "opsmesh-api" in smoke_script
    assert "opsmesh-worker" in smoke_script
    assert ".venv/bin/alembic" in smoke_script
    assert "alembic current" in smoke_script
    assert "OPSMESH_SMOKE_DOCKER_RUNTIME" in smoke_script
    assert "OPSMESH_DOCKER_CHECK_USER" in smoke_script
    assert "docker.service" in smoke_script
    assert 'sudo -n -u "${docker_check_user}" docker info' in smoke_script
    assert "docker exec" not in smoke_script
    assert "OPSMESH_COMPOSE_FILE" not in smoke_script
    assert "OPSMESH_SMOKE_MONITORING" in smoke_script
    assert "/-/ready" in smoke_script
    assert "/api/health" in smoke_script


def test_server_update_script_installs_verified_release_bundle() -> None:
    update_script = read_repo_file("scripts/server-update.sh")

    assert "#!/usr/bin/env sh" in update_script
    assert "update | rollback | restart" in update_script
    assert "--tag" in update_script
    assert "--manifest-url" in update_script
    assert "--manifest-file" in update_script
    assert "--bundle-url" in update_script
    assert "--bundle-file" in update_script
    assert "--bundle-sha256" in update_script
    assert "--image" not in update_script
    assert "--dry-run" in update_script
    assert "opsmesh-server-${tag}-manifest.json" in update_script
    assert "json_value \"bundle.sha256\"" in update_script
    assert "sha256_file" in update_script
    assert "Bundle sha256 mismatch" in update_script
    assert "releases_dir" in update_script
    assert "current_link" in update_script
    assert "switch_current" in update_script
    assert "tar -xzf" in update_script
    assert "release-state.env" in update_script
    assert "uv sync" in update_script
    assert ".venv/bin/alembic upgrade head" in update_script
    assert "restart \"${api_service}\" \"${worker_service}\"" in update_script
    assert "docker compose" not in update_script
    assert "docker pull" not in update_script
    assert "server-smoke-test.sh" in update_script


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
    assert "Validate release bundle manifest" in workflow
    assert "release-bundle/backend/app/main.py" in workflow
    assert "release-bundle/systemd/opsmesh-api.service" in workflow
    assert "release-bundle/systemd/opsmesh-worker.service" in workflow
    assert "deploy/server/docker-compose.backend.yml" not in workflow
    assert "docker build" not in workflow
    assert "docker run" not in workflow
    assert "docker compose" not in workflow
    assert "anchore/sbom-action" not in workflow
    assert "aquasecurity/trivy-action" not in workflow


def test_release_publish_workflow_builds_vps_bundle_without_backend_image() -> None:
    workflow = read_repo_file(".github/workflows/release-publish.yml")

    assert "packages: write" not in workflow
    assert "docker/login-action" not in workflow
    assert "docker/build-push-action" not in workflow
    assert "ghcr.io/jhupo/opsmesh" not in workflow
    assert "push: true" not in workflow
    assert "softprops/action-gh-release" in workflow
    assert "opsmesh-server-${{ github.ref_name }}-manifest.json" in workflow
    assert "opsmesh-server-${{ github.ref_name }}.tar.gz" in workflow
    assert "opsmesh-server-${{ github.ref_name }}.tar.gz.sha256" in workflow
    assert "release-bundle/manifest.json" in workflow
    assert "\"bundle\": {" in workflow
    assert "\"sha256\": \"${bundle_sha256}\"" in workflow
    assert "\"image\"" not in workflow
    assert "\"deployment_mode\": \"systemd\"" in workflow
    assert "deploy/server/monitoring" in workflow


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
        "deploy/server/monitoring/grafana/dashboards/opsmesh-overview.json"
    )

    assert "job_name: opsmesh-api" in prometheus
    assert "metrics_path: /api/v1/metrics" in prometheus
    assert "alertmanager:9093" in prometheus
    assert "/etc/prometheus/rules/*.yml" in prometheus

    for alert_name in (
        "OpsMeshApiDown",
        "OpsMeshHighHttp5xxRate",
        "OpsMeshQueueBacklogHigh",
        "OpsMeshDeadLettersPresent",
        "OpsMeshNoOnlineWorkersWithBacklog",
        "OpsMeshWorkerStale",
        "OpsMeshRuntimeSaturationHigh",
        "OpsMeshRuntimeQuotaHigh",
    ):
        assert alert_name in alert_rules
    assert "opsmesh_http_requests_total" in alert_rules
    assert "opsmesh_queue_jobs" in alert_rules
    assert "opsmesh_workers" in alert_rules
    assert "opsmesh_runtime_saturation_ratio" in alert_rules

    assert "receiver: opsmesh-operators" in alertmanager
    assert "url: http://prometheus:9090" in datasource
    assert "path: /var/lib/grafana/dashboards" in dashboard_provider
    assert '"uid": "opsmesh-control-plane"' in dashboard
    assert "opsmesh_runtime_space_quota_usage_ratio" in dashboard
