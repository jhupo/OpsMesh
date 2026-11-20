"""Validate execution plugin config parsing and lifecycle limits."""

import pytest
from pydantic import ValidationError

from opsmesh.plugins.execution.config import (
    LangfuseSettings,
    Settings,
    TracingSettings,
)


def test_defaults_keep_tracing_disabled():
    settings = Settings()
    assert settings.tracing.destination == "disabled"
    assert settings.tracing.include_sensitive_data is False
    assert settings.cleanup_timeout_seconds == 10.0


def test_removed_statistics_configuration_is_rejected():
    with pytest.raises(ValidationError):
        Settings(host_instance="bad host")


def test_langfuse_destination_requires_settings():
    with pytest.raises(ValidationError):
        Settings(tracing={"destination": "langfuse"})


def test_langfuse_settings_validate_url_and_secret_refs():
    with pytest.raises(ValidationError):
        LangfuseSettings(
            base_url="ftp://langfuse.example.com",
            public_key_ref="LANGFUSE_PUBLIC_KEY",
            secret_key_ref="LANGFUSE_SECRET_KEY",
        )
    with pytest.raises(ValidationError):
        LangfuseSettings(
            base_url="https://langfuse.example.com",
            public_key_ref="LANGFUSE-PUBLIC-KEY",
            secret_key_ref="LANGFUSE_SECRET_KEY",
        )
    settings = LangfuseSettings(
        base_url="https://langfuse.example.com",
        public_key_ref="LANGFUSE_PUBLIC_KEY",
        secret_key_ref="LANGFUSE_SECRET_KEY",
    )
    assert settings.sample_rate == 1.0
    assert settings.export_timeout_seconds == 5.0


def test_tracing_settings_reject_unknown_destination():
    with pytest.raises(ValidationError):
        TracingSettings(destination="remote")
