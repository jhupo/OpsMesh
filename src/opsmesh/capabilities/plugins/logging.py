"""Forward bounded, redacted connector logs to the platform log pipeline."""

import json
import logging

from opsmesh_plugin_sdk.services.observability import PluginLog
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.shared.security.redaction import redact_sensitive_payload


def record_plugin_log(session: Session, principal: PluginPrincipal, request: PluginLog) -> None:
    """Emit an authorized connector log; ordinary log lines do not create audit records."""
    PluginAuthenticationService(session).require(principal, "logs.write")
    metadata = redact_sensitive_payload(request.metadata)
    if len(json.dumps(metadata)) > 8000:
        raise ValueError("Plugin log metadata exceeds limit")
    level = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}.get(
        request.level
    )
    if level is None:
        raise ValueError("Plugin log level is invalid")
    logging.getLogger("opsmesh.plugins").log(
        level,
        request.code,
        extra={
            "plugin_workspace_id": str(principal.workspace_id),
            "plugin_install_id": str(principal.install_id),
            "plugin_credential_id": str(principal.credential_id),
            "plugin_event_id": str(request.event_id) if request.event_id else None,
            "plugin_fields": metadata,
        },
    )
