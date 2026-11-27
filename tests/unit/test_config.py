"""Configuration loading, dotenv precedence, nested env vars, and union types."""

from datetime import timedelta
from ipaddress import IPv4Address

from pydantic import BaseModel, Field
import pytest

from opsmesh.config import BaseSettings, Config, Env, SettingsError


def test_config_defaults_and_env_precedence(tmp_path, monkeypatch):
    base_env = tmp_path / ".env"
    prod_env = tmp_path / ".env.prod"
    base_env.write_text(
        'PORT=8100\nLOG_LEVEL=25\nSUPERUSERS=["console:base"]\n',
        encoding="utf-8",
    )
    prod_env.write_text(
        'PORT=8200\nSUPERUSERS=["console:prod"]\nCUSTOM_FLAG=true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PORT", "8300")
    monkeypatch.setenv("INVOKE_TIMEOUT", "12.5")

    cfg = Config(_env_file=(base_env, prod_env), host="127.0.0.2")
    assert cfg.host == IPv4Address("127.0.0.2")
    # OS env overrides .env.prod which overrides .env
    assert cfg.port == 8300
    assert cfg.invoke_timeout == 12.5
    # union_mode="left_to_right" preserves numeric log level as int
    assert cfg.log_level == 25
    assert isinstance(cfg.log_level, int)
    assert cfg.superusers == {"console:prod"}
    assert cfg.custom_flag is True
    assert cfg.session_expire_timeout == timedelta(minutes=2)


def test_config_log_level_string_and_init_kwargs_override_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=DEBUG\nPORT=9000\n", encoding="utf-8")
    monkeypatch.setenv("PORT", "9001")

    cfg = Config(_env_file=env_file, port=9002)
    assert cfg.log_level == "DEBUG"
    assert cfg.port == 9002


def test_env_reads_environment_from_os_and_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ENVIRONMENT=staging\n", encoding="utf-8")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert Env(_env_file=env_file).environment == "staging"

    monkeypatch.setenv("ENVIRONMENT", "dev")
    assert Env(_env_file=env_file).environment == "dev"


def test_nested_env_explosion_alias_and_complex_json_errors(tmp_path, monkeypatch):
    class DBSettings(BaseModel):
        host: str = "localhost"
        port: int = 5432
        tags: list[str] = Field(default_factory=list)

    class AppSettings(BaseSettings):
        db: DBSettings = Field(default_factory=DBSettings)
        endpoint: str = Field(default="default", alias="service_url")

    env_file = tmp_path / ".env"
    env_file.write_text(
        'DB={"host": "db.internal", "port": 5433}\n'
        'DB__TAGS=["primary", "ro"]\n'
        "EXTRA__NESTED__KEY=42\n"
        'SERVICE_URL="https://api.local"\n',
        encoding="utf-8",
    )
    settings = AppSettings(_env_file=env_file)
    assert settings.db.host == "db.internal"
    assert settings.db.port == 5433
    assert settings.db.tags == ["primary", "ro"]
    assert settings.endpoint == "https://api.local"
    assert settings.extra == {"nested": {"key": 42}}

    bad_env = tmp_path / ".env.bad"
    bad_env.write_text("DB={invalid_json\n", encoding="utf-8")
    with pytest.raises(SettingsError, match='error parsing env var "db"'):
        AppSettings(_env_file=bad_env)
