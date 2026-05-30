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
        "CHAINCLOUD_POSTGRES_PASSWORD",
    ):
        assert setting in env_example


def test_deployment_docs_cover_processes_and_production_guards() -> None:
    docs = read_repo_file("docs/backend-deployment.md")

    assert "API process" in docs
    assert "Worker process" in docs
    assert "Server Test Stack" in docs
    assert "scripts/server-smoke-test.sh" in docs
    assert "CHAINCLOUD_ENABLE_API_DOCS=false" in docs
    assert "CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET" in docs
    assert "CHAINCLOUD_RUN_MIGRATIONS=false" in docs


def test_server_compose_reuses_external_database_network() -> None:
    compose = read_repo_file("deploy/server/docker-compose.backend.yml")

    assert "api:" in compose
    assert "worker:" in compose
    assert "postgres:" not in compose
    assert "redis:" not in compose
    assert "external: true" in compose
    assert "name: ${CHAINCLOUD_BACKEND_NETWORK:-chaincloud_default}" in compose
    assert "${CHAINCLOUD_API_BIND:-127.0.0.1}:${CHAINCLOUD_API_PORT:-8000}:8000" in compose
    assert "CHAINCLOUD_RUN_MIGRATIONS: \"false\"" in compose
    assert "/api/v1/health/ready" in compose


def test_server_env_template_uses_shared_runtime_services() -> None:
    env_example = read_repo_file("deploy/server/env.example")

    assert "chaincloud-postgres:5432" in env_example
    assert "chaincloud-redis:6379" in env_example
    assert "CHAINCLOUD_BACKEND_NETWORK=chaincloud_default" in env_example
    assert "CHAINCLOUD_RELEASE_DIR=/opt/chaincloud-app/current" in env_example
    assert "CHAINCLOUD_ENV_FILE=/opt/chaincloud-app/.env" in env_example
    assert "replace-with-random-token" in env_example
    assert "chaincloud:chaincloud" not in env_example


def test_server_smoke_script_checks_health_and_migrations() -> None:
    smoke_script = read_repo_file("scripts/server-smoke-test.sh")

    assert "#!/usr/bin/env sh" in smoke_script
    assert "/api/v1/health/ready" in smoke_script
    assert "docker compose --env-file" in smoke_script
    assert "alembic current" in smoke_script
