"""Credential-safe model definitions are enforced below transport entry points."""

import re
from typing import Any
from urllib.parse import urlsplit

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.models.api import ModelProfile


def validate_profile(profile: ModelProfile) -> None:
    if not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", profile.secret_ref):
        raise OpsMeshError("CONFIG_INVALID", "Credentials require an environment secret reference")
    if profile.base_url:
        parsed = urlsplit(profile.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise OpsMeshError(
                "CONFIG_INVALID", "Model endpoint must be HTTP(S) without credentials"
            )
    _check_settings(profile.settings)


def _check_settings(value: Any) -> None:
    prohibited = {
        "api_key",
        "x_api_key",
        "password",
        "access_token",
        "refresh_token",
        "authorization",
        "proxy_authorization",
        "client_secret",
        "secret",
        "token",
    }
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold().replace("-", "_") in prohibited:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Model settings cannot persist credential values"
                )
            _check_settings(nested)
    elif isinstance(value, (tuple, list)):
        for nested in value:
            _check_settings(nested)
