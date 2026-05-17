from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfile_defines_non_root_api_runtime() -> None:
    dockerfile = read_repo_file("Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "pip install ." in dockerfile
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
    assert "CHAINCLOUD_ENABLE_API_DOCS=false" in docs
    assert "CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET" in docs
    assert "CHAINCLOUD_RUN_MIGRATIONS=false" in docs
