import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")




def test_self_hosted_connector_smoke_script_is_packaged_and_redacted() -> None:
    script = read_repo_file("scripts/self-hosted-connector-smoke.sh")
    docs = read_repo_file("docs/self-hosted-connector.md")

    assert "python3" in script
    assert "-m venv" in script
    assert "pip install" in script
    assert "opsmesh-self-hosted-worker" in script
    assert "--check" in script
    assert "--once" in script
    assert "OPSMESH_RUNTIME_CREDENTIAL" in script
    assert "response bodies" not in script
    assert "self-hosted-connector-smoke.sh" in docs
    assert "OPSMESH_CONNECTOR_EXPECTED_STATUS=completed" in docs


def test_compose_declares_api_worker_and_dependencies() -> None:
    compose = read_repo_file("docker-compose.yml")

    for service in ("api:", "worker:", "postgres:", "redis:"):
        assert service in compose
    assert "OPSMESH_DATABASE_URL" in compose
    assert "OPSMESH_REDIS_URL" in compose
    assert "/app/.opsmesh-storage" in compose
    assert 'OPSMESH_RUN_MIGRATIONS: "false"' in compose
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
        "OPSMESH_AUDIT_INTEGRITY_CHECK_INTERVAL_SECONDS",
        "OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT",
        "OPSMESH_OTEL_TRACE_SAMPLE_RATIO",
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
    assert "for isolated task runtimes" in docs
    assert "The API process must not be able to control the Docker daemon" in docs
    assert "pinned observability stack" in docs
    assert "OPSMESH_SMOKE_MONITORING=true" in docs
    assert "scripts/server-smoke-test.sh" in docs
    assert "opsmesh-api.service" in docs
    assert "opsmesh-worker.service" in docs
    assert "opsmesh-api" in docs
    assert "opsmesh-worker" in docs
    assert "Only the worker receives Docker authority" in docs
    assert "Do not make immutable release directories writable" in docs
    assert "uv sync" not in docs
    assert ".venv/" not in docs
    assert "opsmesh-server migrate" in docs
    api_unit = read_repo_file("deploy/server/systemd/opsmesh-api.service")
    worker_unit = read_repo_file("deploy/server/systemd/opsmesh-worker.service")
    assert "ExecStart=/opt/opsmesh/current/opsmesh-server api" in api_unit
    assert "ExecStart=/opt/opsmesh/current/opsmesh-server worker" in worker_unit
    assert "SupplementaryGroups=docker" in worker_unit
    assert "SupplementaryGroups=docker" not in api_unit
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
    assert "OPSMESH_ENV_FILE=/opt/opsmesh/.env" in env_example
    assert "OPSMESH_MONITORING_DIR=/opt/opsmesh/current/deploy/server/monitoring" in env_example
    assert "OPSMESH_WORKER_HEARTBEAT_TOKEN=replace-with-random-token" in env_example
    assert "OPSMESH_SERVICE_NAME=opsmesh-backend" in env_example
    assert "OPSMESH_STORAGE_ROOT=/var/lib/opsmesh/storage" in env_example
    assert "OPSMESH_READINESS_WORKER_CHECK_ENABLED=true" in env_example
    assert "OPSMESH_EXTERNAL_CALL_MAX_ATTEMPTS=2" in env_example
    assert "OPSMESH_AUDIT_EVENT_WORM_ENABLED=true" in env_example
    assert "OPSMESH_AUDIT_INTEGRITY_CHECK_INTERVAL_SECONDS=3600" in env_example
    assert "OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317" in env_example
    assert "OPSMESH_ALERTMANAGER_CONFIG_FILE=" in env_example
    assert 'OPSMESH_RUNTIME_ALLOWED_IMAGES=["opsmesh-runtime:local"]' in env_example
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
    assert "./opsmesh-server migrate current" in smoke_script
    assert "-m dotenv" in smoke_script
    assert ".venv/" not in smoke_script
    assert "OPSMESH_SMOKE_DOCKER_RUNTIME" in smoke_script
    assert "OPSMESH_DOCKER_CHECK_USER" in smoke_script
    assert "docker.service" in smoke_script
    assert 'sudo -n -u "${docker_check_user}" docker info' in smoke_script
    assert "docker exec" not in smoke_script
    assert "OPSMESH_COMPOSE_FILE" not in smoke_script
    assert "OPSMESH_SMOKE_MONITORING" in smoke_script
    assert "/-/ready" in smoke_script
    assert "/api/health" in smoke_script
    assert "opsmesh-observability" in smoke_script
    assert "/loki/api/v1/query_range" in smoke_script
    assert "/api/traces/" in smoke_script
    assert "opsmesh_audit_integrity_workspaces" in smoke_script
    assert "opsmesh_model_usage_records_24h" in smoke_script
    assert 'query={service_name="opsmesh-api"}' in smoke_script



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
    dashboard = read_repo_file("deploy/server/monitoring/grafana/dashboards/opsmesh-overview.json")

    assert "job_name: opsmesh-api" in prometheus
    assert "metrics_path: /api/v1/metrics" in prometheus
    assert "127.0.0.1:9093" in prometheus
    assert "/etc/prometheus/rules/*.yml" in prometheus

    for alert_name in (
        "OpsMeshApiDown",
        "OpsMeshHighHttp5xxRate",
        "OpsMeshHighHttpLatency",
        "OpsMeshQueueBacklogHigh",
        "OpsMeshDeadLettersPresent",
        "OpsMeshNoOnlineWorkersWithBacklog",
        "OpsMeshWorkerStale",
        "OpsMeshRuntimeSaturationHigh",
        "OpsMeshRuntimeQuotaHigh",
        "OpsMeshAuditIntegrityInvalid",
        "OpsMeshAuditIntegrityNotCurrent",
        "OpsMeshModelUsageUnpriced",
        "OpsMeshCostBudgetExhausted",
        "OpsMeshObservabilityTargetDown",
        "OpsMeshMetricsCollectionFailed",
        "OpsMeshTelemetryExportFailed",
        "OpsMeshAlertDeliveryFailed",
    ):
        assert alert_name in alert_rules
    assert "opsmesh_http_requests_total" in alert_rules
    assert "clamp_min(sum(rate(opsmesh_http_requests_total[5m])), 0.000001)" in alert_rules
    assert "sum(increase(opsmesh_http_requests_total[5m])) >= 20" in alert_rules
    assert "opsmesh_queue_jobs" in alert_rules
    assert "opsmesh_workers" in alert_rules
    assert "opsmesh_runtime_saturation_ratio" in alert_rules
    assert 'absent(opsmesh_metrics_collection_success{source="postgres"})' in alert_rules
    assert 'absent(opsmesh_metrics_collection_success{source="redis"})' in alert_rules

    assert "receiver: opsmesh-operators" in alertmanager
    assert "url: http://127.0.0.1:9090" in datasource
    assert "path: /var/lib/grafana/dashboards" in dashboard_provider
    assert '"uid": "opsmesh-control-plane"' in dashboard
    assert "opsmesh_runtime_space_quota_usage_ratio" in dashboard
    assert "opsmesh_http_request_duration_ms_bucket" in dashboard
    assert "opsmesh_model_cost_current_month" in dashboard
    assert "opsmesh_metrics_collection_success" in dashboard


def test_observability_stack_is_pinned_persistent_and_correlated() -> None:
    compose = read_repo_file("deploy/server/monitoring/docker-compose.yml")
    collector = read_repo_file("deploy/server/monitoring/otel-collector.yml")
    tempo = read_repo_file("deploy/server/monitoring/tempo.yml")
    datasource = read_repo_file(
        "deploy/server/monitoring/grafana/provisioning/datasources/prometheus.yml"
    )
    dashboard = json.loads(
        read_repo_file("deploy/server/monitoring/grafana/dashboards/opsmesh-overview.json")
    )

    for service in (
        "prometheus:",
        "alertmanager:",
        "loki:",
        "tempo:",
        "otel-collector:",
        "grafana:",
    ):
        assert service in compose
    for tag in (
        "prom/prometheus:v3.10.0",
        "prom/alertmanager:v0.33.1",
        "grafana/loki:3.7.2",
        "grafana/tempo:2.10.7",
        "otel/opentelemetry-collector-contrib:0.157.0",
        "grafana/grafana:13.2.1",
    ):
        assert tag in compose
    assert ":latest" not in compose
    for volume in (
        "prometheus_data:/prometheus",
        "alertmanager_data:/alertmanager",
        "loki_data:/loki",
        "tempo_data:/var/tempo",
        "grafana_data:/var/lib/grafana",
    ):
        assert volume in compose
    assert "journald:" not in collector
    assert "receivers:\n        - otlp" in collector
    assert "/var/log/journal" not in compose
    assert compose.count("network_mode: host") == 6
    assert 'user: "0:0"' not in compose
    assert "host.docker.internal" not in compose
    assert "--web.listen-address=127.0.0.1:9090" in compose
    assert "GF_SERVER_HTTP_ADDR: 127.0.0.1" in compose
    assert "otlp/tempo:" in collector
    assert "otlphttp/loki:" in collector
    assert "endpoint: 127.0.0.1:4317" in collector
    assert "endpoint: 127.0.0.1:14317" in collector
    assert "service-graphs" in tempo
    assert "span-metrics" in tempo
    assert "send_exemplars: true" in tempo
    assert "tracesToLogsV2" in datasource
    assert "derivedFields" in datasource
    assert 'opsmesh-(api|worker)' in json.dumps(dashboard)
    assert dashboard["uid"] == "opsmesh-control-plane"


def test_observability_yaml_assets_parse_and_wire_required_pipelines() -> None:
    compose = yaml.safe_load(read_repo_file("deploy/server/monitoring/docker-compose.yml"))
    collector = yaml.safe_load(read_repo_file("deploy/server/monitoring/otel-collector.yml"))
    prometheus = yaml.safe_load(read_repo_file("deploy/server/monitoring/prometheus.yml"))
    alerts = yaml.safe_load(read_repo_file("deploy/server/monitoring/alert-rules.yml"))
    loki = yaml.safe_load(read_repo_file("deploy/server/monitoring/loki.yml"))
    tempo = yaml.safe_load(read_repo_file("deploy/server/monitoring/tempo.yml"))

    assert set(compose["services"]) == {
        "prometheus",
        "alertmanager",
        "loki",
        "tempo",
        "otel-collector",
        "grafana",
    }
    assert all(
        service["network_mode"] == "host" for service in compose["services"].values()
    )
    assert all("ports" not in service for service in compose["services"].values())
    assert "user" not in compose["services"]["otel-collector"]
    assert collector["service"]["pipelines"]["logs"]["receivers"] == ["otlp"]
    assert collector["service"]["pipelines"]["traces"]["receivers"] == ["otlp"]
    assert collector["service"]["pipelines"]["logs"]["exporters"] == ["otlphttp/loki"]
    assert collector["service"]["pipelines"]["traces"]["exporters"] == ["otlp/tempo"]
    assert prometheus["rule_files"] == ["/etc/prometheus/rules/*.yml"]
    assert prometheus["scrape_configs"][1]["static_configs"][0]["targets"] == [
        "127.0.0.1:8000"
    ]
    assert alerts["groups"][0]["rules"]
    assert loki["limits_config"]["allow_structured_metadata"] is True
    assert tempo["overrides"]["defaults"]["metrics_generator"]["processors"] == [
        "service-graphs",
        "span-metrics",
    ]


def test_alertmanager_renderer_requires_real_receiver_and_keeps_secret_out_of_output(
    tmp_path: Path,
) -> None:
    script = ROOT / "scripts/render-alertmanager-config.py"
    output = tmp_path / "alertmanager.generated.yml"
    env = os.environ.copy()
    env.update(
        {
            "OPSMESH_ALERT_WEBHOOK_URL": "https://alerts.example.test/opsmesh",
            "OPSMESH_ALERT_WEBHOOK_BEARER_TOKEN": "receiver-secret",
        }
    )
    rendered = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert rendered.returncode == 0, rendered.stderr
    content = output.read_text(encoding="utf-8")
    assert 'url: "https://alerts.example.test/opsmesh"' in content
    assert 'credentials: "receiver-secret"' in content
    assert "receiver-secret" not in rendered.stdout
    assert "alerts.example.test" not in rendered.stdout

    rejected = subprocess.run(
        [
            sys.executable,
            str(script),
            "--output",
            str(output),
            "--webhook-url",
            "http://alerts.example.test/insecure",
        ],
        cwd=ROOT,
        env={key: value for key, value in env.items() if key != "OPSMESH_ALERT_WEBHOOK_URL"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "must use HTTPS" in rejected.stderr


def test_systemd_services_use_distinct_telemetry_service_names() -> None:
    api_unit = read_repo_file("deploy/server/systemd/opsmesh-api.service")
    worker_unit = read_repo_file("deploy/server/systemd/opsmesh-worker.service")

    assert api_unit.count("Environment=OPSMESH_SERVICE_NAME=opsmesh-api") == 1
    assert worker_unit.count("Environment=OPSMESH_SERVICE_NAME=opsmesh-worker") == 1


def test_audit_migration_enforces_database_level_worm_protection() -> None:
    migration = read_repo_file("backend/migrations/versions/0055_observability_cost_and_audit.py")

    assert "CREATE TRIGGER trg_opsmesh_protect_audit_events" in migration
    assert "BEFORE UPDATE OR DELETE ON audit_events" in migration
    assert "opsmesh.audit_retention_delete" in migration
    assert "DROP TRIGGER IF EXISTS trg_opsmesh_protect_audit_events" in migration


def test_postgres_data_backfill_rejects_offline_migration() -> None:
    rendered = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "heads", "--sql"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert rendered.returncode != 0
    assert "requires an online database connection" in rendered.stderr
