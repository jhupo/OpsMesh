from opsmesh.shared.security.redaction import redact_sensitive_payload


def public_run_event_metadata(value: dict[str, object]) -> dict[str, object]:
    """Detailed operator evidence is available only through its authorized endpoint."""
    return redact_sensitive_payload(
        {key: item for key, item in value.items() if key != "diagnostic"}
    )
