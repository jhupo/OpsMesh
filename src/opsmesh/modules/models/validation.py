"""Shared credential-reference rules for model catalog and transport callers."""

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from opsmesh.framework.errors import OpsMeshError

if TYPE_CHECKING:
    from .api import ModelProfile


def validate_model_profile(profile: "ModelProfile") -> None:
    pattern = (
        r"credential:[0-9a-f]{32}@[1-9][0-9]*"
        if profile.owner_id
        else r"env:[A-Za-z_][A-Za-z0-9_]*"
    )
    if not re.fullmatch(pattern, profile.secret_ref):
        raise OpsMeshError("CONFIG_INVALID", "Model credential reference has an invalid scope")
    if profile.base_url:
        try:
            parsed = urlsplit(profile.base_url)
            _ = parsed.port
        except ValueError:
            raise OpsMeshError("CONFIG_INVALID", "Model endpoint is invalid") from None
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


def validate_personal_endpoint(profile: "ModelProfile", allowed_hosts: tuple[str, ...]) -> None:
    """Users select an endpoint from deployment-approved hosts, without granting network access."""
    validate_model_profile(profile)
    endpoint = urlsplit(profile.base_url or "https://api.openai.com/v1")
    if endpoint.scheme != "https" or endpoint.port not in {None, 443}:
        raise OpsMeshError("FORBIDDEN", "Personal model endpoints require HTTPS on port 443")
    host = endpoint.hostname
    if host is None or host.casefold() not in {item.casefold() for item in allowed_hosts}:
        raise OpsMeshError(
            "FORBIDDEN", "Personal model endpoint is not approved by this deployment"
        )


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
